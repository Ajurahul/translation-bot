# syntax=docker/dockerfile:1

########################################
# Stage 1: build Python dependencies
########################################
FROM python:3.10-slim AS builder

# git is needed only to pip-install the mega.py dependency straight from
# GitHub (see requirements.txt); build-essential covers any package that
# needs to compile a C extension. Neither is needed at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

########################################
# Stage 2: runtime image
########################################
FROM python:3.10-slim AS runtime

# Chrome, for cogs/crawler.py's headless Selenium scraping.
# Selenium 4.15's built-in Selenium Manager auto-detects this install and
# downloads a matching chromedriver on its own -- no manual driver pinning.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        ca-certificates \
        fonts-liberation \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Bring in the Python packages built in the previous stage.
COPY --from=builder /install /usr/local

WORKDIR /app

# Pre-download the nltk corpora the bot needs at startup (core/bot.py calls
# nltk.download("brown"/"punkt"/"popular") on boot). Baking them in here
# means the container doesn't depend on nltk's servers being reachable, and
# the startup call becomes an instant local cache hit instead of a network
# call sitting on the event loop.
ENV NLTK_DATA=/usr/local/share/nltk_data
RUN python -m nltk.downloader -d "$NLTK_DATA" brown punkt popular

COPY . .

# Run as a non-root user; the bot writes scratch files (per-user .txt
# exports) and logs into its working directory, so it needs to be writable.
RUN useradd --create-home --shell /usr/sbin/nologin botuser \
    && mkdir -p /app/logs \
    && chown -R botuser:botuser /app
USER botuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 docker/healthcheck.py

# Assumes main.py lives at the repo root (referenced by
# scripts/auto-restart.sh as `python3 main.py`) -- it wasn't in the archive
# you gave me, so make sure it's present in your build context.
CMD ["python3", "main.py"]
