#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
export OPENAI_API_KEY="not-needed"
exec python3 run.py
