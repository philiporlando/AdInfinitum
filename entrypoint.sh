#!/bin/sh
set -e

# Clean up any stale Xvfb locks from previous runs
rm -f /tmp/.X*-lock

# Start virtual display
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &

export DISPLAY=:99

# Give Xvfb a moment to initialize
sleep 3

exec uv run python -u main.py