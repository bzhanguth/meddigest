# MedDigest 📋

Weekly oncology & rare-disease news digest. Fetches the past week of regulatory,
clinical-trial, and rare-disease news from public RSS/ClinicalTrials.gov sources,
summarizes each item with a **local Ollama model**, and renders a styled HTML
report plus an optional **MP3 audio** and **MP4 video** digest (one slide per item).

Everything runs locally — no paid APIs, no cloud LLM. Summarization uses whatever
Ollama model you have pulled (default `llama3.2:latest`).

## What it covers

- **Regulatory** — FDA approvals, accelerated/breakthrough/priority-review designations, EMA news
- **Trials** — Phase 2/3 results from ClinicalTrials.gov and oncology journals
- **Rare disease** — orphan-drug pipeline and rare-disorder updates
- **Methods** — Bayesian, adaptive, platform, and basket trial design spotlights

A brand-focus mode (e.g. Roche/Genentech) filters the feed to one company's
pipeline for competitive-intelligence or interview prep.

## Setup

Requires Python 3.10+, [Ollama](https://ollama.com) running locally, and `ffmpeg`
(for MP4 output).

```bash
cd ~/meddigest
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ollama pull llama3.2          # or any model you prefer
```

## Usage

CLI:

```bash
.venv/bin/python meddigest.py                 # current week
.venv/bin/python meddigest.py --days 14       # look back 14 days
.venv/bin/python meddigest.py --no-mp3        # skip audio (faster)
.venv/bin/python meddigest.py --model mistral:latest
```

Streamlit UI (generate + browse the archive, with inline preview and download buttons):

```bash
.venv/bin/streamlit run app.py
```

## Output

Each run writes to `output/<week>/`:

| File | Description |
|------|-------------|
| `index.html` | Styled digest report |
| `digest.mp3` | Audio digest (Edge TTS) |
| `digest.mp4` | Video digest, one slide per item |
| `digest.txt` | Plain-text TTS script |

`output/` is git-ignored — digests are regenerated on demand.

## Tech

Python · feedparser · requests · Ollama (local LLM) · edge-tts · ffmpeg · Streamlit
