# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:/root/.local/bin:$PATH"

# ── Firefox + all libs needed to run headless as root ────────────────────────
RUN apt-get update && apt-get install -y \
    firefox-esr \
    wget \
    ca-certificates \
    tar \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# ── geckodriver ───────────────────────────────────────────────────────────────
RUN GECKODRIVER_VERSION="0.36.0" && \
    wget -q -O /tmp/geckodriver.tar.gz \
    "https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz" && \
    tar -xz -C /usr/local/bin -f /tmp/geckodriver.tar.gz && \
    chmod +x /usr/local/bin/geckodriver && \
    rm /tmp/geckodriver.tar.gz

# ── AdNauseam ─────────────────────────────────────────────────────────────────
RUN mkdir -p /extensions && \
    wget -q -O /extensions/adnauseam.xpi \
    "https://github.com/dhowe/AdNauseam/releases/download/v3.28.2/adnauseam-3.28.2.firefox.zip"

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY browse.py ./

ENV SESSION_DURATION=300 \
    PAUSE_BETWEEN=60 \
    ADNAUSEAM_XPI=/extensions/adnauseam.xpi \
    MOZ_ALLOW_DOWNGRADE=1

CMD ["uv", "run", "python", "browse.py"]