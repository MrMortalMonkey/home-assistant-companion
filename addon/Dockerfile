ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM $BUILD_FROM

# System dependencies
RUN apk add --no-cache python3 py3-pip py3-requests jq curl ffmpeg

# Python dependencies
RUN pip3 install --no-cache-dir --break-system-packages \
    anthropic \
    openai \
    matplotlib \
    websocket-client

# Startup script
COPY run.sh /
RUN chmod a+x /run.sh

# Application code
COPY app/ /app/

WORKDIR /app
CMD ["/run.sh"]
