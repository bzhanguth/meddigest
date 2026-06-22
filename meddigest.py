#!/usr/bin/env python3
"""
meddigest.py — Weekly Medical News Digest
Fetches oncology & rare-disease news, summarizes with local Ollama,
and renders a local HTML report + MP3 audio digest.

Usage:
    python meddigest.py              # current week
    python meddigest.py --days 14   # look back 14 days
    python meddigest.py --no-mp3    # skip audio generation
"""

import asyncio
import json
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import requests
import edge_tts

# ── Configuration ─────────────────────────────────────────────────────────────

OUTPUT_DIR = Path.home() / "meddigest" / "output"
OLLAMA_URL  = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:latest"
VOICE_ID    = "en-US-AriaNeural"
LOOKBACK_DAYS = 7

FOCUS_KEYWORDS = [
    "oncol", "cancer", "tumor", "tumour", "carcinoma", "lymphoma", "leukemia",
    "melanoma", "sarcoma", "glioma", "myeloma", "adenocarcinoma",
    "rare disease", "orphan drug", "orphan disease", "rare disorder",
    "fda approv", "accelerated approval", "breakthrough therapy",
    "priority review", "fast track", "nda approval", "bla approval",
    "phase 2", "phase 3", "phase ii", "phase iii",
    "immunotherapy", "checkpoint inhibitor", "car-t", "cell therapy",
    "gene therapy", "gene editing", "crispr",
    "targeted therapy", "precision medicine", "biomarker",
    "overall survival", "progression-free", "objective response",
    "complete response", "remission", "clinical trial result",
]

# ── Focus filters ─────────────────────────────────────────────────────────────
# Brand-focused mode: filter to items mentioning a specific company / its
# pipeline. Used for interview prep or competitive intelligence.

FOCUS_FILTERS = {
    "roche-genentech": {
        "label":    "Roche & Genentech",
        "slug":     "roche-genentech",
        "keywords": [
            "roche", "genentech", "chugai", "hoffmann-la roche",
            # Marketed brands
            "tecentriq", "atezolizumab", "alecensa", "alectinib",
            "polivy", "polatuzumab", "kadcyla", "trastuzumab emtansine",
            "perjeta", "pertuzumab", "phesgo", "herceptin",
            "rozlytrek", "entrectinib", "lunsumio", "mosunetuzumab",
            "columvi", "glofitamab", "vabysmo", "faricimab",
            "ocrevus", "ocrelizumab", "evrysdi", "risdiplam",
            "esbriet", "pirfenidone", "xolair", "omalizumab",
            "actemra", "tocilizumab", "hemlibra", "emicizumab",
            "itovebi", "inavolisib", "elevidys",
        ],
        "sponsors": ["Roche", "Genentech", "Hoffmann-La Roche", "Chugai"],
        # Google News RSS search queries (broad → narrow)
        "news_queries": [
            ("Roche oncology",            "news"),
            ("Genentech FDA approval",    "regulatory"),
            ("Roche clinical trial",      "trials"),
            ("Genentech pipeline",        "news"),
            ("Roche Phase 3",             "trials"),
            ("Genentech breast cancer",   "trials"),
            ("Roche immunotherapy",       "trials"),
            ("Roche acquisition",         "news"),
        ],
    },
}

METHODS_KEYWORDS = [
    "adaptive design", "adaptive trial", "adaptive randomization",
    "bayesian", "platform trial", "basket trial", "umbrella trial",
    "master protocol", "response adaptive", "seamless design",
    "mams ", "multi-arm multi-stage", "interim analysis", "futility stopping",
    "dose escalation", "3+3 design", "boin ", "continual reassessment",
    "randomization", "stratified randomization", "propensity score",
    "survival analysis", "cox proportional", "time-to-event",
    "causal inference", "estimand", "intention-to-treat",
    "enrichment design", "biomarker-driven",
]

SOURCES = [
    # ── Regulatory ──────────────────────────────────────────────────────────
    dict(name="FDA Press Releases",  cat="regulatory", always=True,
         url="https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
    dict(name="FDA Drug Safety",     cat="regulatory", always=False,
         url="https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drug-safety-communications/rss.xml"),
    dict(name="EMA News",            cat="regulatory", always=False,
         url="https://www.ema.europa.eu/en/news/news-rss-feed"),

    # ── Journals ────────────────────────────────────────────────────────────
    dict(name="NEJM",                cat="journal", always=False,
         url="https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss"),
    dict(name="Lancet",              cat="journal", always=False,
         url="https://www.thelancet.com/rssfeed/lancet_current.xml"),
    dict(name="Lancet Oncology",     cat="journal", always=False,
         url="https://www.thelancet.com/rssfeed/lanonc_current.xml"),
    dict(name="Nature Medicine",     cat="journal", always=False,
         url="https://www.nature.com/nm.rss"),
    dict(name="JCO",                 cat="journal", always=False,
         url="https://ascopubs.org/action/showFeed?type=etoc&feed=rss&jc=jco"),
    dict(name="JAMA Oncology",       cat="journal", always=False,
         url="https://jamanetwork.com/rss/site_3/68.xml"),
    dict(name="Cancer Cell",         cat="journal", always=False,
         url="https://www.cell.com/cancer-cell/current.rss"),
    dict(name="Blood",               cat="journal", always=False,
         url="https://ashpublications.org/rss/site_1/1.xml"),

    # ── News ────────────────────────────────────────────────────────────────
    dict(name="STAT News",           cat="news", always=False,
         url="https://www.statnews.com/feed/"),
    dict(name="BioPharma Dive",      cat="news", always=False,
         url="https://www.biopharmadive.com/feeds/news/"),
    dict(name="NCI Cancer.gov",      cat="news", always=True,
         url="https://www.cancer.gov/syndication/rss/news"),
    dict(name="Fierce Biotech",      cat="news", always=False,
         url="https://www.fiercebiotech.com/rss/xml"),
    dict(name="Endpoints News",      cat="news", always=False,
         url="https://endpts.com/feed/"),

    # ── Rare Disease ────────────────────────────────────────────────────────
    dict(name="NORD Rare Diseases",  cat="rare", always=True,
         url="https://rarediseases.org/feed/"),
    dict(name="Global Genes",        cat="rare", always=False,
         url="https://globalgenes.org/feed/"),
    dict(name="Orphanet News",       cat="rare", always=False,
         url="https://www.orpha.net/consor4.01/www/cgi-bin/home.php?Lng=EN&stapage=rss"),
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_relevant(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in FOCUS_KEYWORDS)

def has_methods(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in METHODS_KEYWORDS)

def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)

def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

_CITATION_RE = re.compile(
    r'\([A-Z0-9][\w\-]*(?:/[\w\-]+)+\)'         # trial ID combos: (LITESPARK-034/LS-034)
    r'|Volume\s+\d+,?\s*Issue\s+\d+(?:[,\s]+Pages?\s+[\d\-]+)?'  # Vol/Issue/Page
    r'|\bPages?\s+\d[\d\-]*\b'                   # standalone page refs
    r'|\b10\.\d{4,}/\S+'                         # DOIs
    r'|\b(?:January|February|March|April|May|June|July|August|September|October|November|December'
    r'|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4}',  # "April 23, 2026"
    re.IGNORECASE,
)

def clean_summary(s: str) -> str:
    s = _CITATION_RE.sub("", s)
    return clean_ws(s)

def parse_entry_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            return datetime.fromtimestamp(time.mktime(val), tz=timezone.utc)
    return None

# ── Fetching ──────────────────────────────────────────────────────────────────

def fetch_rss(source: dict, cutoff: datetime) -> list[dict]:
    try:
        feed = feedparser.parse(source["url"])
        items = []
        for e in feed.entries:
            pub = parse_entry_date(e)
            if pub and pub < cutoff:
                continue
            title   = clean_ws(e.get("title", ""))
            summary = clean_summary(strip_html(e.get("summary", e.get("description", ""))))[:500]
            full    = f"{title} {summary}"
            if not (source["always"] or is_relevant(full)):
                continue
            items.append({
                "title":   title,
                "url":     e.get("link", ""),
                "summary": summary,
                "pub":     pub.strftime("%b %d, %Y") if pub else "Recent",
                "source":  source["name"],
                "cat":     source["cat"],
                "methods": has_methods(full),
            })
        return items[:15]  # cap per source to avoid journal ETOCs flooding
    except Exception as exc:
        print(f"  [skip] {source['name']}: {exc}", file=sys.stderr)
        return []


def fetch_clinicaltrials(lookback_days: int) -> list[dict]:
    cutoff_str = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    # ClinicalTrials.gov v2: query.cond for condition, query.term for keywords
    # filter.resultsFirst and format are not valid v2 params
    params = {
        "query.cond": "oncology OR cancer OR rare disease",
        "query.term": "Phase 2 OR Phase 3",
        "sort":       "LastUpdatePostDate:desc",
        "pageSize":   "15",
    }
    try:
        r = requests.get(
            "https://clinicaltrials.gov/api/v2/studies",
            params=params,  # let requests handle encoding (no manual urlencode)
            timeout=20,
        )
        r.raise_for_status()
        items = []
        for s in r.json().get("studies", []):
            proto = s.get("protocolSection", {})
            id_m     = proto.get("identificationModule", {})
            status_m = proto.get("statusModule", {})
            desc_m   = proto.get("descriptionModule", {})
            cond_m   = proto.get("conditionsModule", {})

            last_update = status_m.get("lastUpdatePostDateStruct", {}).get("date", "")
            if last_update and last_update < cutoff_str:
                continue

            nct        = id_m.get("nctId", "")
            brief      = id_m.get("briefTitle", "")
            summary    = clean_summary(desc_m.get("briefSummary", ""))[:400]
            conditions = ", ".join(cond_m.get("conditions", [])[:3])
            title      = f"{brief} [{conditions}]" if conditions else brief

            items.append({
                "title":   clean_ws(title),
                "url":     f"https://clinicaltrials.gov/study/{nct}",
                "summary": clean_ws(summary),
                "pub":     last_update,
                "source":  "ClinicalTrials.gov",
                "cat":     "trials",
                "methods": has_methods(summary),
            })
        return items
    except Exception as exc:
        print(f"  [skip] ClinicalTrials.gov: {exc}", file=sys.stderr)
        return []

def fetch_google_news(query: str, cat: str, cutoff: datetime, cap: int = 50) -> list[dict]:
    """Search Google News via its public RSS endpoint. Used in focus mode."""
    from urllib.parse import quote_plus
    url = ("https://news.google.com/rss/search?"
           f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en")
    src = {"name": f"GoogleNews: {query}", "cat": cat, "always": True, "url": url}
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries:
        pub = parse_entry_date(e)
        if pub and pub < cutoff:
            continue
        title   = clean_ws(e.get("title", ""))
        summary = clean_summary(strip_html(e.get("summary", e.get("description", ""))))[:500]
        full    = f"{title} {summary}"
        items.append({
            "title":   title,
            "url":     e.get("link", ""),
            "summary": summary,
            "pub":     pub.strftime("%b %d, %Y") if pub else "Recent",
            "source":  "Google News",
            "cat":     cat,
            "methods": has_methods(full),
        })
    return items[:cap]


def fetch_clinicaltrials_sponsored(sponsors: list[str], cutoff: datetime, cap: int = 50) -> list[dict]:
    """Pull trials sponsored by named companies from ClinicalTrials.gov v2."""
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    out: list[dict] = []
    for sponsor in sponsors:
        params = {
            "query.lead":  sponsor,
            "sort":        "LastUpdatePostDate:desc",
            "pageSize":    str(cap),
        }
        try:
            r = requests.get(
                "https://clinicaltrials.gov/api/v2/studies",
                params=params, timeout=20,
            )
            r.raise_for_status()
            for s in r.json().get("studies", []):
                proto    = s.get("protocolSection", {})
                id_m     = proto.get("identificationModule", {})
                status_m = proto.get("statusModule", {})
                desc_m   = proto.get("descriptionModule", {})
                cond_m   = proto.get("conditionsModule", {})

                last_update = status_m.get("lastUpdatePostDateStruct", {}).get("date", "")
                if last_update and last_update < cutoff_str:
                    continue

                nct        = id_m.get("nctId", "")
                brief      = id_m.get("briefTitle", "")
                summary    = clean_summary(desc_m.get("briefSummary", ""))[:400]
                conditions = ", ".join(cond_m.get("conditions", [])[:3])
                title      = f"{brief} [{conditions}]" if conditions else brief

                out.append({
                    "title":   clean_ws(title),
                    "url":     f"https://clinicaltrials.gov/study/{nct}",
                    "summary": clean_ws(summary),
                    "pub":     last_update,
                    "source":  f"CT.gov · {sponsor}",
                    "cat":     "trials",
                    "methods": has_methods(summary),
                })
        except Exception as exc:
            print(f"  [skip] CT.gov ({sponsor}): {exc}", file=sys.stderr)
    return out


# ── Ollama ────────────────────────────────────────────────────────────────────

def ollama(prompt: str) -> str:
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=180,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as exc:
        print(f"  [warn] Ollama: {exc}", file=sys.stderr)
        return ""


def build_intro(items: list[dict]) -> str:
    top = "\n".join(f"- {it['title']}" for it in items[:18])
    return ollama(
        "You are writing the spoken introduction for a weekly medical news podcast "
        "aimed at a biostatistics PhD student specializing in oncology and rare disease. "
        "Write exactly 3–4 conversational sentences summarizing the most important themes "
        "from this week's headlines. Do NOT list individual drugs by name. "
        "Do NOT include URLs, citations, or numbers. Sound natural when read aloud.\n\n"
        f"Headlines:\n{top}\n\nIntroduction:"
    )


def ollama_enrich_items(items: list[dict]) -> list[dict]:
    """Add 'insight' field to top items per category (1 Ollama call per section)."""
    items = [dict(it) for it in items]
    for cat in _SEC_ORDER:
        cat_indices = [i for i, it in enumerate(items) if it.get("cat") == cat][:4]
        if not cat_indices:
            continue
        cat_items = [items[i] for i in cat_indices]
        numbered  = "\n".join(
            f"{j+1}. {it['title']}. {it['summary'][:180]}"
            for j, it in enumerate(cat_items)
        )
        label = _CAT_LABEL.get(cat, cat.upper())
        response = ollama(
            f"You are a clinical pharmacology expert reviewing {label} news.\n"
            "For each item below write exactly ONE sentence explaining why it matters "
            "clinically or for drug development — impact on patients, trials, or the field. "
            "Be specific: name the drug class, disease, or population affected. "
            "Output ONLY numbered lines (1. 2. etc.), no preamble or extra text.\n\n"
            f"{numbered}\n\nInsights:"
        )
        if response:
            for line in re.split(r"\n+", response):
                line = line.strip()
                m = re.match(r"^(\d+)[.)]\s*(.+)", line)
                if m:
                    j = int(m.group(1)) - 1
                    if 0 <= j < len(cat_indices):
                        items[cat_indices[j]]["insight"] = m.group(2)
    return items


def build_methods_spotlight(items: list[dict]) -> str:
    method_items = [it for it in items if it["methods"]][:6]
    if not method_items:
        return ""
    lines = "\n".join(
        f"- [{it['source']}] {it['title']}: {it['summary'][:200]}"
        for it in method_items
    )
    return ollama(
        "You are a biostatistics expert. The following medical news items mention "
        "statistical or trial design methods. Write a 3–4 sentence 'Methods Spotlight' "
        "for a PhD student: identify the key design(s) used this week "
        "(e.g., Bayesian adaptive, basket trial, platform trial, seamless Phase 2/3), "
        "explain why each is methodologically interesting, and note any implications "
        "for trial efficiency or FWER control. Be specific and educational.\n\n"
        f"Items:\n{lines}\n\nMethods Spotlight:"
    )

# ── HTML ──────────────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, 'Segoe UI', Helvetica, sans-serif;
       background: #eef2f7; color: #1a1a2e; }
header { background: #0d2137; color: white; padding: 2rem 2.5rem; }
header h1 { font-size: 1.75rem; font-weight: 800; letter-spacing: -.02em; }
header p  { opacity: .65; margin-top: .35rem; font-size: .9rem; }
.intro { background: #163454; color: #cce7ff; padding: 1.4rem 2.5rem;
         font-size: .98rem; line-height: 1.75; border-left: 4px solid #4fc3f7; }
main  { max-width: 980px; margin: 2rem auto; padding: 0 1.25rem; }
.sec  { margin-bottom: 2.5rem; }
.sec-title { font-size: 1.05rem; font-weight: 800; color: #0d2137;
             border-bottom: 2px solid #4fc3f7; padding-bottom: .4rem;
             margin-bottom: 1rem; text-transform: uppercase; letter-spacing: .06em; }
.sec-title.methods { color: #7b2d00; border-color: #ed8936; }
.methods-box { background: #fffaf0; border: 1px solid #f6ad55;
               border-radius: 8px; padding: 1.1rem 1.3rem;
               line-height: 1.75; color: #4a2008; font-size: .93rem; }
.card { background: white; border-radius: 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,.07);
        padding: 1rem 1.2rem; margin-bottom: .75rem;
        display: flex; gap: 1rem; align-items: flex-start; }
.card:hover { box-shadow: 0 3px 12px rgba(0,0,0,.11); }
.badge { font-size: .65rem; font-weight: 700; padding: .2rem .55rem;
         border-radius: 4px; white-space: nowrap; flex-shrink: 0; margin-top: .2rem; }
.b-regulatory { background: #bee3f8; color: #1a5276; }
.b-journal    { background: #c6f6d5; color: #1e4d2b; }
.b-trials     { background: #fefcbf; color: #744210; }
.b-news       { background: #e9d8fd; color: #44306a; }
.b-rare       { background: #fed7e2; color: #7d1f3c; }
.card-body h3 { font-size: .93rem; font-weight: 600; margin-bottom: .3rem; }
.card-body h3 a { color: #163454; text-decoration: none; }
.card-body h3 a:hover { text-decoration: underline; color: #2980b9; }
.card-body p  { font-size: .83rem; color: #555; line-height: 1.55; }
.meta { font-size: .72rem; color: #aaa; margin-top: .3rem; }
.insight { font-size: .82rem; color: #1a6a9a; font-style: italic;
           margin-top: .35rem; padding-left: .6rem;
           border-left: 2px solid #4fc3f7; line-height: 1.5; }
.mpill { display: inline-block; background: #fbd38d; color: #7b2d00;
         font-size: .62rem; padding: .1rem .4rem; border-radius: 3px;
         margin-left: .45rem; font-weight: 700; vertical-align: middle; }
footer { text-align: center; color: #aaa; font-size: .78rem; padding: 2.5rem 1rem; }
"""

_SEC_META = {
    "regulatory": ("b-regulatory", "FDA &amp; Regulatory Actions"),
    "journal":    ("b-journal",    "Journal Highlights"),
    "trials":     ("b-trials",     "Clinical Trial Updates"),
    "news":       ("b-news",       "Industry &amp; Pipeline News"),
    "rare":       ("b-rare",       "Rare Disease"),
}
_SEC_ORDER = ["regulatory", "trials", "journal", "news", "rare"]


def render_html(items: list[dict], intro: str, methods: str, week_label: str) -> str:
    grouped: dict[str, list] = {c: [] for c in _SEC_ORDER}
    for it in items:
        cat = it["cat"] if it["cat"] in grouped else "news"
        grouped[cat].append(it)

    sections_html = ""
    for cat in _SEC_ORDER:
        cat_items = grouped[cat]
        if not cat_items:
            continue
        badge_cls, title = _SEC_META[cat]
        cards = ""
        for it in cat_items:
            mpill = '<span class="mpill">METHODS</span>' if it["methods"] else ""
            cards += (
                f'<div class="card">'
                f'<span class="badge {badge_cls}">{it["source"]}</span>'
                f'<div class="card-body">'
                f'<h3><a href="{it["url"]}" target="_blank">{it["title"]}</a>{mpill}</h3>'
                f'<p>{it["summary"]}</p>'
                + (f'<p class="insight">💡 {it["insight"]}</p>' if it.get("insight") else "")
                + f'<div class="meta">{it["pub"]}</div>'
                f'</div></div>\n'
            )
        sections_html += f'<div class="sec"><div class="sec-title">{title}</div>\n{cards}</div>\n'

    if methods:
        sections_html += (
            '<div class="sec">'
            '<div class="sec-title methods">🧮 Methods Spotlight</div>'
            f'<div class="methods-box">{methods}</div>'
            '</div>\n'
        )

    intro_html = f'<div class="intro">{intro}</div>\n' if intro else ""
    total = len(items)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return (
        f'<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<title>Medical Digest — {week_label}</title>\n'
        f'<style>{_CSS}</style>\n</head>\n<body>\n'
        f'<header><h1>📋 Medical News Digest</h1>'
        f'<p>Week of {week_label} &nbsp;·&nbsp; Oncology &amp; Rare Disease '
        f'&nbsp;·&nbsp; {total} items</p></header>\n'
        f'{intro_html}'
        f'<main>\n{sections_html}</main>\n'
        f'<footer>Generated {generated} &nbsp;·&nbsp; '
        f'Sources: FDA, ClinicalTrials.gov, NEJM, Lancet, STAT News &amp; more</footer>\n'
        f'</body>\n</html>'
    )

# ── TTS ───────────────────────────────────────────────────────────────────────

_SEC_SPOKEN = {
    "regulatory": "FDA and Regulatory Actions",
    "trials":     "Clinical Trial Updates",
    "journal":    "Journal Highlights",
    "news":       "Industry and Pipeline News",
    "rare":       "Rare Disease News",
}

def build_tts_script(items: list[dict], intro: str, methods: str, week_label: str) -> str:
    parts = [f"Medical News Digest. Week of {week_label}.\n"]
    if intro:
        parts.append(intro.rstrip(".") + ".\n")

    grouped: dict[str, list] = {c: [] for c in _SEC_ORDER}
    for it in items:
        cat = it["cat"] if it["cat"] in grouped else "news"
        grouped[cat].append(it)

    for cat in _SEC_ORDER:
        cat_items = grouped[cat][:4]  # cap at 4 per section for audio length
        if not cat_items:
            continue
        parts.append(f"\n{_SEC_SPOKEN[cat]}.\n")
        for it in cat_items:
            summary = re.sub(r"https?://\S+", "", it["summary"])
            summary = re.sub(r"\s+", " ", summary).strip()
            insight = it.get("insight", "")
            text    = f"{it['title']}. {summary}"
            if insight:
                text += f" {insight}"
            parts.append(text + "\n")

    if methods:
        parts.append(f"\nMethods Spotlight.\n{methods}\n")

    parts.append("\nThat concludes this week's medical news digest.")

    script = " ".join(parts)
    # Final clean for TTS
    script = re.sub(r"<[^>]+>", " ", script)
    script = re.sub(r"&amp;", "and", script)
    script = re.sub(r"&[a-z]+;", " ", script)
    script = re.sub(r"\s+", " ", script).strip()
    return script


async def _synth(script: str, out_path: Path):
    communicate = edge_tts.Communicate(script, VOICE_ID, rate="+0%", pitch="+1Hz")
    with open(out_path, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])


def synthesize_mp3(script: str, out_path: Path):
    # edge_tts can struggle with very long inputs; chunk at ~4000 chars
    CHUNK = 4000
    if len(script) <= CHUNK:
        asyncio.run(_synth(script, out_path))
        return

    # Split on sentence boundaries near the chunk limit
    sentences = re.split(r"(?<=[.!?])\s+", script)
    chunks, buf = [], ""
    for sent in sentences:
        if len(buf) + len(sent) + 1 > CHUNK and buf:
            chunks.append(buf.strip())
            buf = sent
        else:
            buf += (" " if buf else "") + sent
    if buf:
        chunks.append(buf.strip())

    audio_parts = []
    for i, chunk in enumerate(chunks):
        tmp = out_path.with_suffix(f".part{i}.mp3")
        asyncio.run(_synth(chunk, tmp))
        audio_parts.append(tmp)

    # Concatenate parts
    with open(out_path, "wb") as fh:
        for part in audio_parts:
            fh.write(part.read_bytes())
            part.unlink()

# ── Video ─────────────────────────────────────────────────────────────────────

# Slide colour palette (matches HTML theme)
_BG       = (13,  33,  55)   # dark navy
_WHITE    = (255, 255, 255)
_ACCENT   = (79,  195, 247)  # light blue
_MUTED    = (160, 190, 215)
_CAT_CLR  = {
    "regulatory": (41,  128, 185),
    "journal":    (39,  174, 96),
    "trials":     (243, 156, 18),
    "news":       (142, 68,  173),
    "rare":       (231, 76,  60),
    "methods":    (230, 126, 34),
}
_CAT_LABEL = {
    "regulatory": "FDA & REGULATORY",
    "journal":    "JOURNAL HIGHLIGHTS",
    "trials":     "CLINICAL TRIALS",
    "news":       "INDUSTRY & PIPELINE",
    "rare":       "RARE DISEASE",
    "methods":    "METHODS SPOTLIGHT",
}
W, H = 1280, 720


def _find_font(size: int):
    from PIL import ImageFont
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(text: str, chars: int) -> str:
    return "\n".join(textwrap.fill(line, chars) for line in text.splitlines())


def _draw_slide(title: str, cat: str, lines: list[tuple[str, str]],
                week_label: str) -> "PIL.Image.Image":
    """Render one 1280×720 slide. lines = [(heading, subtext), ...]"""
    from PIL import Image, ImageDraw
    img  = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    color = _CAT_CLR.get(cat, _ACCENT)

    # Top accent bar
    draw.rectangle([0, 0, W, 8], fill=color)

    # Section label pill
    lbl_font = _find_font(18)
    draw.rounded_rectangle([48, 28, 48 + 260, 28 + 34], radius=6, fill=color)
    draw.text((58, 33), _CAT_LABEL.get(cat, cat.upper()), font=lbl_font, fill=_WHITE)

    # Title
    title_font = _find_font(38)
    draw.text((48, 80), _wrap(title, 52), font=title_font, fill=_WHITE)

    # Divider
    draw.rectangle([48, 148, W - 48, 151], fill=color)

    # Items
    item_title_font   = _find_font(22)
    item_summary_font = _find_font(18)
    y = 168
    for heading, subtext in lines[:4]:
        if y > H - 80:
            break
        # Bullet dot
        draw.ellipse([48, y + 7, 58, y + 17], fill=color)
        # Heading
        wrapped_h = _wrap(heading, 72)
        draw.text((70, y), wrapped_h, font=item_title_font, fill=_WHITE)
        y += 28 * (wrapped_h.count("\n") + 1)
        # Subtext (max 2 lines)
        if subtext:
            wrapped_s = _wrap(subtext[:180], 95)
            lines_s   = wrapped_s.splitlines()[:2]
            draw.text((70, y), "\n".join(lines_s), font=item_summary_font, fill=_MUTED)
            y += 24 * len(lines_s) + 6
        y += 14  # gap between items

    # Footer
    foot_font = _find_font(16)
    draw.text((W - 48, H - 30), f"MedDigest  ·  {week_label}",
              font=foot_font, fill=_MUTED, anchor="rs")
    # Bottom accent bar
    draw.rectangle([0, H - 6, W, H], fill=color)

    return img


def _title_slide(week_label: str, n_items: int) -> "PIL.Image.Image":
    from PIL import Image, ImageDraw
    img  = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 10], fill=_ACCENT)
    draw.rectangle([0, H - 10, W, H], fill=_ACCENT)

    draw.text((W // 2, 200), "📋  MedDigest",
              font=_find_font(64), fill=_WHITE, anchor="mm")
    draw.text((W // 2, 290), "Weekly Oncology & Rare Disease News",
              font=_find_font(28), fill=_ACCENT, anchor="mm")
    draw.text((W // 2, 380), f"Week of  {week_label}",
              font=_find_font(36), fill=_WHITE, anchor="mm")
    draw.text((W // 2, 450), f"{n_items} items this week",
              font=_find_font(22), fill=_MUTED, anchor="mm")
    return img


def _end_slide(week_label: str) -> "PIL.Image.Image":
    from PIL import Image, ImageDraw
    img  = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 10], fill=_ACCENT)
    draw.rectangle([0, H - 10, W, H], fill=_ACCENT)
    draw.text((W // 2, H // 2 - 40), "That's all for this week.",
              font=_find_font(44), fill=_WHITE, anchor="mm")
    draw.text((W // 2, H // 2 + 30), "MedDigest  ·  " + week_label,
              font=_find_font(24), fill=_MUTED, anchor="mm")
    return img


def _item_slide(item: dict, week_label: str) -> "PIL.Image.Image":
    """Render one 1280×720 slide for a single news item."""
    from PIL import Image, ImageDraw
    cat   = item.get("cat", "news")
    color = _CAT_CLR.get(cat, _ACCENT)
    img   = Image.new("RGB", (W, H), _BG)
    draw  = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 8], fill=color)

    # Category badge + source/date
    lbl_font  = _find_font(17)
    label_txt = _CAT_LABEL.get(cat, cat.upper())
    badge_w   = min(len(label_txt) * 10 + 24, 300)
    draw.rounded_rectangle([48, 26, 48 + badge_w, 60], radius=5, fill=color)
    draw.text((58, 33), label_txt, font=lbl_font, fill=_WHITE)
    meta_txt = f"{item.get('source', '')}  ·  {item.get('pub', '')}"
    draw.text((48 + badge_w + 18, 34), meta_txt, font=lbl_font, fill=_MUTED)

    # Title
    title_font    = _find_font(32)
    wrapped_title = _wrap(item["title"][:120], 52)
    draw.text((48, 82), wrapped_title, font=title_font, fill=_WHITE)
    n_title_lines = wrapped_title.count("\n") + 1
    y = 82 + n_title_lines * 44 + 10

    # Divider
    draw.rectangle([48, y, W - 48, y + 3], fill=color)
    y += 18

    # Summary
    summary = re.sub(r"https?://\S+", "", item.get("summary", "")).strip()
    if summary:
        sum_font    = _find_font(20)
        wrapped_sum = _wrap(summary[:280], 80)
        lines_sum   = wrapped_sum.splitlines()[:4]
        draw.text((48, y), "\n".join(lines_sum), font=sum_font, fill=(210, 225, 240))
        y += 26 * len(lines_sum) + 14

    # Insight (why it matters) — in accent colour
    insight = item.get("insight", "")
    if insight and y < H - 110:
        ins_font    = _find_font(19)
        draw.ellipse([48, y + 5, 60, y + 17], fill=color)
        wrapped_ins = _wrap(insight[:220], 84)
        lines_ins   = wrapped_ins.splitlines()[:3]
        draw.text((68, y), "\n".join(lines_ins), font=ins_font, fill=_ACCENT)

    # Footer
    foot_font = _find_font(16)
    draw.text((W - 48, H - 30), f"MedDigest  ·  {week_label}",
              font=foot_font, fill=_MUTED, anchor="rs")
    draw.rectangle([0, H - 6, W, H], fill=color)
    return img


def _audio_duration(mp3_path: Path) -> float:
    """Return MP3 duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(mp3_path)],
        capture_output=True, text=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def render_video(items: list[dict], intro: str, methods: str,
                 week_label: str, mp3_path: Path, out_path: Path):
    from PIL import Image

    # One slide per item, capped at 4 per category so video stays manageable
    grouped: dict[str, list] = {c: [] for c in _SEC_ORDER}
    for it in items:
        cat = it["cat"] if it["cat"] in grouped else "news"
        grouped[cat].append(it)

    video_items = []
    for cat in _SEC_ORDER:
        video_items.extend(grouped[cat][:4])

    # slide_specs: ("title"|"item"|"end", item_dict_or_None)
    slide_specs: list[tuple] = [("title", None)]
    for it in video_items:
        slide_specs.append(("item", it))
    if methods:
        slide_specs.append(("item", {
            "cat": "methods", "source": "", "pub": week_label,
            "title": "Methods Spotlight",
            "summary": methods[:350], "insight": "",
        }))
    slide_specs.append(("end", None))

    total_secs = _audio_duration(mp3_path)
    secs_each  = total_secs / len(slide_specs)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir      = Path(tmp)
        concat_f     = tmp_dir / "slides.txt"
        concat_lines = []

        for i, (kind, data) in enumerate(slide_specs):
            png = tmp_dir / f"slide_{i:03d}.png"
            if kind == "title":
                img = _title_slide(week_label, len(items))
            elif kind == "end":
                img = _end_slide(week_label)
            else:
                img = _item_slide(data, week_label)
            img.save(str(png))
            concat_lines.append(f"file '{png}'\nduration {secs_each:.3f}")

        last_png = tmp_dir / f"slide_{len(slide_specs)-1:03d}.png"
        concat_lines.append(f"file '{last_png}'")
        concat_f.write_text("\n".join(concat_lines))

        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_f),
            "-i", str(mp3_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(out_path),
        ], check=True, capture_output=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global OLLAMA_MODEL
    parser = argparse.ArgumentParser(description="Weekly medical news digest")
    parser.add_argument("--days",   type=int, default=LOOKBACK_DAYS, help="Lookback days (default 7)")
    parser.add_argument("--no-mp3",   action="store_true", help="Skip MP3 generation")
    parser.add_argument("--no-video", action="store_true", help="Skip MP4 generation")
    parser.add_argument("--model",    default=OLLAMA_MODEL, help="Ollama model (default llama3.2:latest)")
    parser.add_argument("--focus",    choices=sorted(FOCUS_FILTERS.keys()),
                        help="Brand-focused digest (e.g., roche-genentech)")
    parser.add_argument("--since",    help="ISO date YYYY-MM-DD (overrides --days)")
    args = parser.parse_args()
    OLLAMA_MODEL = args.model

    focus = FOCUS_FILTERS.get(args.focus) if args.focus else None

    if args.since:
        cutoff_local = datetime.fromisoformat(args.since)
        cutoff = cutoff_local.replace(tzinfo=timezone.utc)
        span_days = (datetime.now(timezone.utc) - cutoff).days
        label_span = f"since {args.since}"
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        span_days = args.days
        label_span = f"last {args.days} days"

    if focus:
        week_label = f"{focus['label']} · {label_span}"
        week_slug  = f"{focus['slug']}_{datetime.now().strftime('%Y%m%d')}"
    else:
        week_label = datetime.now().strftime("%B %d, %Y")
        week_slug  = datetime.now().strftime("%Y-W%W")
    out_dir = OUTPUT_DIR / week_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[meddigest] {week_label}  ({label_span}, {span_days}d)")

    all_items: list[dict] = []

    if focus:
        # Focus mode: Google News searches + sponsored CT.gov trials, then filter.
        for query, cat in focus["news_queries"]:
            print(f"  GoogleNews: {query}...", end=" ", flush=True)
            batch = fetch_google_news(query, cat, cutoff, cap=80)
            print(f"{len(batch)} items")
            all_items.extend(batch)

        print(f"  CT.gov (sponsors: {', '.join(focus['sponsors'])})...", end=" ", flush=True)
        ct = fetch_clinicaltrials_sponsored(focus["sponsors"], cutoff, cap=80)
        print(f"{len(ct)} items")
        all_items.extend(ct)
    else:
        for src in SOURCES:
            print(f"  {src['name']}...", end=" ", flush=True)
            batch = fetch_rss(src, cutoff)
            print(f"{len(batch)} items")
            all_items.extend(batch)

        print("  ClinicalTrials.gov...", end=" ", flush=True)
        ct = fetch_clinicaltrials(span_days)
        print(f"{len(ct)} items")
        all_items.extend(ct)

    if focus:
        kws = focus["keywords"]
        before = len(all_items)
        all_items = [
            it for it in all_items
            if any(kw in (it["title"] + " " + it["summary"]).lower() for kw in kws)
        ]
        print(f"  [focus] keyword filter: {len(all_items)} / {before} kept")

    # Deduplicate by normalised title prefix
    seen, unique = set(), []
    for it in all_items:
        key = re.sub(r"\W+", " ", it["title"].lower())[:70]
        if key not in seen:
            seen.add(key)
            unique.append(it)

    print(f"\n  {len(unique)} unique items ({len(all_items)} raw)")

    if not unique:
        print("[meddigest] No items found — check network / RSS URLs.")
        return

    print("\n[meddigest] Ollama: enriching item summaries (why it matters)...")
    unique = ollama_enrich_items(unique)

    print("[meddigest] Ollama: generating intro...")
    intro = build_intro(unique)

    print("[meddigest] Ollama: generating methods spotlight...")
    methods = build_methods_spotlight(unique)

    print("[meddigest] Rendering HTML...")
    html = render_html(unique, intro, methods, week_label)
    html_path = out_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    txt_path = out_dir / "digest.txt"
    script = build_tts_script(unique, intro, methods, week_label)
    txt_path.write_text(script, encoding="utf-8")

    mp3_path = out_dir / "digest.mp3"
    if not args.no_mp3:
        print("[meddigest] Synthesizing MP3 (edge_tts)...")
        synthesize_mp3(script, mp3_path)
        print(f"  MP3 : {mp3_path}")

    if not args.no_video:
        if mp3_path.exists():
            print("[meddigest] Rendering MP4 (Pillow + ffmpeg)...")
            mp4_path = out_dir / "digest.mp4"
            try:
                render_video(unique, intro, methods, week_label, mp3_path, mp4_path)
                print(f"  MP4 : {mp4_path}")
            except Exception as exc:
                print(f"  [warn] MP4 skipped: {exc}", file=sys.stderr)
        else:
            print("[meddigest] Skipping MP4 — no MP3 (run without --no-mp3 first)")

    print(f"\n[meddigest] Done!  Output → {out_dir}")
    print(f"  HTML: {html_path}")
    print(f"  Text: {txt_path}")
    print(f"\n  Open in browser:  open '{html_path}'")

if __name__ == "__main__":
    main()
