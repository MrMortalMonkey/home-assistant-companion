FROM python:3.12-slim

# Minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies (cached separately from the app for faster rebuilds)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY assistant.py        .
COPY deploy_server.py    .
COPY config.py           .
COPY shared.py           .
COPY skills.py           .
COPY llm_provider.py     .
COPY i18n.py             .
COPY behavior.txt    .
COPY KNOWN_APPLIANCES.json .

# Persistent directory for config.json, memory.db, and logs
VOLUME ["/app/data"]
ENV CONFIG_PATH=/app/data/config.json \
    DB_PATH=/app/data/memory.db \
    LOG_PATH=/app/data/assistant.log

# Simple healthcheck: assistant process is still alive
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "assistant.py" >/dev/null || exit 1

CMD ["python3", "-u", "assistant.py"]
