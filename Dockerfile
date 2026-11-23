# ReviewPulse API image (spec 06). The image holds code, configs and dependencies only.
# releases/ and state/ are mounted as volumes; RP_USER_KEY is passed as an env var.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RP_HOME=/app \
    MLFLOW_DISABLE_AGENT_HINT=1

WORKDIR /app
RUN pip install --no-cache-dir uv

# dependencies first (CPU-only torch comes from tool.uv.sources in pyproject.toml)
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache . && rm -rf /root/.cache

COPY configs ./configs

RUN useradd --create-home --uid 1000 rp \
    && mkdir -p /app/state /app/releases \
    && chown -R rp:rp /app/state /app/releases
USER rp

VOLUME ["/app/state", "/app/releases"]
EXPOSE 8000
CMD ["reviewpulse", "serve", "--host", "0.0.0.0", "--port", "8000"]
