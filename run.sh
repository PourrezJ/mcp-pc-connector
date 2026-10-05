#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || { python -m venv .venv && ./.venv/Scripts/pip install -r requirements.txt; }
exec ./.venv/Scripts/python.exe server.py
