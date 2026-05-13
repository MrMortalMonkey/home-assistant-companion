ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM $BUILD_FROM

# System dependencies
RUN apk add --no-cache python3 py3-pip py3-requests jq curl ffmpeg

# Python dependencies
RUN pip3 install --no-cache-dir --break-system-packages \
    anthropic \
    openai \
    matplotlib

# Startup script
COPY run.sh /
RUN chmod a+x /run.sh

# Application code
COPY assistant.py        /app/
COPY deploy_server.py    /app/
COPY config.py           /app/
COPY shared.py           /app/
COPY skills.py           /app/
COPY llm_provider.py     /app/
COPY i18n.py             /app/
COPY behavior.txt    /app/
COPY KNOWN_APPLIANCES.json /app/

WORKDIR /app
CMD ["/run.sh"]
