# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# --- Build-time Env ---
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.13 \
    PATH="/app/.venv/bin:/root/.local/bin:$PATH"

# --- Optimized Dependency Install ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    firefox-esr \
    wget \
    ca-certificates \
    xvfb \
    xauth \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libgbm1 \
    libasound2 \
    && GECKODRIVER_VERSION="0.36.0" \
    && wget -q -O /tmp/geckodriver.tar.gz \
    "https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz" \
    && tar -xz -C /usr/local/bin -f /tmp/geckodriver.tar.gz \
    && chmod +x /usr/local/bin/geckodriver \
    && rm -rf /tmp/* /var/lib/apt/lists/*

# --- AdNauseam Extension ---
RUN mkdir -p /extensions \
    && wget -q -O /extensions/adnauseam.xpi \
    "https://github.com/dhowe/AdNauseam/releases/download/v3.28.2/adnauseam-3.28.2.firefox.zip"

WORKDIR /app

# --- Project Setup ---
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY urls.json ./src/main.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

# --- Healthcheck ---
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD test -f /tmp/heartbeat || exit 1

ENTRYPOINT ["./entrypoint.sh"]