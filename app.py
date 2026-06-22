"""
app.py — MedDigest Streamlit UI
Run:  streamlit run ~/meddigest/app.py
"""

import re
import subprocess
from pathlib import Path

import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

MEDDIGEST_DIR = Path(__file__).parent
OUTPUT_DIR    = MEDDIGEST_DIR / "output"
PYTHON        = str(MEDDIGEST_DIR / ".venv" / "bin" / "python")
SCRIPT        = str(MEDDIGEST_DIR / "meddigest.py")

st.set_page_config(
    page_title="MedDigest",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "generating" not in st.session_state:
    st.session_state.generating = False

# ── Helpers ───────────────────────────────────────────────────────────────────

def list_weeks() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted([d for d in OUTPUT_DIR.iterdir() if d.is_dir()], reverse=True)

def week_label(week_dir: Path) -> str:
    html = week_dir / "index.html"
    if html.exists():
        m = re.search(r"Week of ([A-Za-z]+ \d+, \d+)", html.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return week_dir.name   # fallback: "2026-W16"

def item_count(week_dir: Path) -> str:
    html = week_dir / "index.html"
    if html.exists():
        m = re.search(r"·\s*(\d+) items", html.read_text(encoding="utf-8"))
        if m:
            return f"  ({m.group(1)} items)"
    return ""

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 📋 MedDigest")
    st.caption("Weekly oncology & rare disease news")
    st.divider()

    st.markdown("### ⚙️ Generate")
    days   = st.slider("Look back (days)", 7, 28, 7, step=7)
    no_mp3  = st.checkbox("Skip MP3 (faster)", value=False)
    no_video = st.checkbox("Skip MP4 (faster)", value=False)
    model  = st.selectbox(
        "Ollama model",
        ["llama3.2:latest", "mistral:latest", "llama3.1:latest"],
        index=0,
    )

    if st.button("🔄 Generate New Digest", type="primary", use_container_width=True):
        st.session_state.generating = True

    st.divider()

    weeks = list_weeks()
    st.markdown(f"### 🗂 Archive — {len(weeks)} week{'s' if len(weeks) != 1 else ''}")

    if weeks:
        options     = [w.name for w in weeks]
        format_func = lambda k: week_label(OUTPUT_DIR / k) + item_count(OUTPUT_DIR / k)
        selected_key = st.radio(
            "week",
            options=options,
            format_func=format_func,
            label_visibility="collapsed",
        )
        selected_dir = OUTPUT_DIR / selected_key
    else:
        selected_dir = None
        st.info("No digests yet — generate one above!")

# ── Generation ────────────────────────────────────────────────────────────────

if st.session_state.generating:
    st.session_state.generating = False
    cmd = [PYTHON, SCRIPT, "--days", str(days), "--model", model]
    if no_mp3:
        cmd.append("--no-mp3")
    if no_video or no_mp3:  # video requires mp3
        cmd.append("--no-video")

    with st.status("⏳ Generating digest…", expanded=True) as status:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            st.write(line.rstrip())
        proc.wait()

    if proc.returncode == 0:
        status.update(label="✅ Digest ready!", state="complete", expanded=False)
        st.rerun()
    else:
        status.update(label="❌ Generation failed — check terminal for errors", state="error")

# ── Main display ──────────────────────────────────────────────────────────────

if selected_dir and selected_dir.exists():
    html_file = selected_dir / "index.html"
    mp3_file  = selected_dir / "digest.mp3"
    mp4_file  = selected_dir / "digest.mp4"
    txt_file  = selected_dir / "digest.txt"

    label = week_label(selected_dir)

    # Header
    st.markdown(f"## 📋 {label}")

    # Download buttons — one per format
    d1, d2, d3, d4, _pad = st.columns([1, 1, 1, 1, 3])
    with d1:
        if html_file.exists():
            st.download_button("⬇ HTML", html_file.read_bytes(),
                file_name=f"meddigest-{selected_dir.name}.html",
                mime="text/html", use_container_width=True)
    with d2:
        if mp3_file.exists():
            st.download_button("⬇ MP3", mp3_file.read_bytes(),
                file_name=f"meddigest-{selected_dir.name}.mp3",
                mime="audio/mpeg", use_container_width=True)
    with d3:
        if mp4_file.exists():
            st.download_button("⬇ MP4", mp4_file.read_bytes(),
                file_name=f"meddigest-{selected_dir.name}.mp4",
                mime="video/mp4", use_container_width=True)
    with d4:
        if txt_file.exists():
            st.download_button("⬇ TXT", txt_file.read_bytes(),
                file_name=f"meddigest-{selected_dir.name}.txt",
                mime="text/plain", use_container_width=True)

    st.divider()

    # Media players
    media_col, _ = st.columns([3, 1])
    with media_col:
        if mp4_file.exists():
            st.markdown("**🎬 Video Digest**")
            st.video(str(mp4_file))
        elif mp3_file.exists():
            st.markdown("**🔊 Audio Digest**")
            st.audio(str(mp3_file), format="audio/mp3")

        if mp3_file.exists() and mp4_file.exists():
            st.markdown("**🔊 Audio only**")
            st.audio(str(mp3_file), format="audio/mp3")

    st.divider()

    # Open in browser (best rendering for the full styled HTML)
    if html_file.exists():
        ocol1, ocol2, _ = st.columns([1.4, 1, 4])
        with ocol1:
            if st.button("🌐 Open in Browser", use_container_width=True):
                subprocess.Popen(["open", str(html_file)])
        with ocol2:
            st.caption(str(html_file))

    st.divider()

    # Tabs: inline preview + raw text
    tab_html, tab_txt = st.tabs(["📄 Inline Preview", "📝 Text Script"])

    with tab_html:
        if html_file.exists():
            st.html(html_file.read_text(encoding="utf-8"))
        else:
            st.warning("No HTML found for this week.")

    with tab_txt:
        if txt_file.exists():
            st.text_area(
                "TTS script",
                txt_file.read_text(encoding="utf-8"),
                height=600,
                label_visibility="collapsed",
            )
        else:
            st.warning("No text script found for this week.")

else:
    # Welcome screen
    st.markdown("## 👋 Welcome to MedDigest")
    st.markdown(
        "Your weekly oncology & rare disease news digest — "
        "summarized by local AI, delivered as HTML + audio."
    )
    st.divider()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("##### 🏛 Regulatory\nFDA approvals, accelerated approvals, breakthrough designations, EMA news")
    with c2:
        st.markdown("##### 🔬 Trials\nPhase 2/3 results from ClinicalTrials.gov, JCO, Lancet Oncology")
    with c3:
        st.markdown("##### 🧬 Rare Disease\nNORD, Global Genes, orphan drug pipeline updates")
    with c4:
        st.markdown("##### 🧮 Methods\nSpotlight on Bayesian, adaptive, platform, and basket trial designs")

    st.divider()
    st.markdown("**→ Click _Generate New Digest_ in the sidebar to get started.**")
