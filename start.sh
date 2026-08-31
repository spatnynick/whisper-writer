#!/bin/bash
cd "$(dirname "$0")"
export OPENAI_API_KEY="not-needed"
exec venv/bin/python3 run.py
