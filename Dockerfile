# =============================================================================
# VIDEO-FACTORY Docker Image
# Zero-dependency Node.js + ffmpeg production image
# =============================================================================

FROM node:20-slim

LABEL maintainer="VIDEO-FACTORY Team"
LABEL version="3.0"
LABEL description="AI带货视频工厂 — 300-Agent蜂群自进化引擎"

# Prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# 1. System dependencies: ffmpeg + curl (healthcheck) + fonts
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ffprobe \
    curl \
    ca-certificates \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Verify ffmpeg installation
RUN ffmpeg -version | head -1

# ---------------------------------------------------------------------------
# 2. Application workspace
# ---------------------------------------------------------------------------
WORKDIR /app

# ---------------------------------------------------------------------------
# 3. Dependency installation (layer caching)
# ---------------------------------------------------------------------------
COPY package.json package-lock.json* ./
RUN npm ci --production --ignore-scripts \
    && npm cache clean --force

# ---------------------------------------------------------------------------
# 4. Application code
# ---------------------------------------------------------------------------
COPY live-server.js ./
COPY core/ ./core/
COPY web/public/ ./web/public/
COPY bin/ ./bin/
COPY hq/ ./hq/
COPY MASTER/ ./MASTER/
COPY presets/ ./presets/
COPY references/ ./references/
COPY start-swarm.js ./

# ---------------------------------------------------------------------------
# 5. Runtime directories
# ---------------------------------------------------------------------------
RUN mkdir -p .uvg-out temp hq-data logs \
    && chmod -R 755 .uvg-out temp hq-data logs

# ---------------------------------------------------------------------------
# 6. Non-root user for security
# ---------------------------------------------------------------------------
RUN groupadd -r videofactory && useradd -r -g videofactory -s /bin/bash videofactory \
    && chown -R videofactory:videofactory /app
USER videofactory

# ---------------------------------------------------------------------------
# 7. Network
# ---------------------------------------------------------------------------
EXPOSE 8088

# ---------------------------------------------------------------------------
# 8. Health check
# ---------------------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -fsS http://localhost:8088/api/health || exit 1

# ---------------------------------------------------------------------------
# 9. Environment defaults
# ---------------------------------------------------------------------------
ENV NODE_ENV=production
ENV UVG_PORT=8088
ENV UVG_OUTPUT_DIR=/app/.uvg-out
ENV UVG_TEMP_DIR=/app/temp

# ---------------------------------------------------------------------------
# 10. Start
# ---------------------------------------------------------------------------
CMD ["node", "live-server.js"]
