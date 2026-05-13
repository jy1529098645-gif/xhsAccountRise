#!/usr/bin/env bash
# One-click launcher for macOS/Linux.
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "Creating venv..."
    python3 -m venv .venv
    echo "Installing deps..."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env — fill ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY then re-run."
    ${EDITOR:-nano} .env
    exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PYTHONPATH="$PWD"

echo "Running migrations..."
.venv/bin/python -m studio migrate

echo "Opening frontend in 3s..."
( sleep 3 && (open 'https://jy1529098645-gif.github.io/xhsAccountRise/' 2>/dev/null || xdg-open 'https://jy1529098645-gif.github.io/xhsAccountRise/' 2>/dev/null) ) &

echo "Starting backend on http://127.0.0.1:8765 ..."
.venv/bin/python -m studio serve --port 8765
