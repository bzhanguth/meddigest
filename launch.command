#!/bin/bash
# Double-click this file in Finder to open MedDigest
cd "$(dirname "$0")"
.venv/bin/streamlit run app.py --server.headless false --browser.gatherUsageStats false
