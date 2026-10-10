#!/bin/bash
# Double-click to run Aptus. Opens in your browser; close this window to quit.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up (about a minute)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
(sleep 2 && open "http://127.0.0.1:8765") &
.venv/bin/python app.py
