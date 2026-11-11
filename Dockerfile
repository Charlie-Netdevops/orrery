# Orrery - one small container. Build: docker compose up -d --build
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin orrery

# Explicit allowlist: only the app is copied - never .env, data or tests.
COPY app.py .
COPY static ./static
COPY config ./config

USER orrery
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

# 0.0.0.0 is *inside* the container only. Which host address it is published on is
# decided by docker-compose.yml (127.0.0.1 by default, see ORRERY_BIND).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--no-server-header"]
