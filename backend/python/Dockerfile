FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/python/requirements*.txt ./
COPY backend/python/app ./app
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then cat /run/secrets/proxy_ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 \
    && PIP_CERT=/etc/ssl/certs/ca-certificates.crt pip install --no-cache-dir -r requirements-ml.txt \
    && chmod -R a+rX /app \
    && useradd --uid 1000 --create-home worker \
    && mkdir -p /data/videos /data/audio /models \
    && chmod 0755 /data /data/videos /data/audio \
    && chown 33:33 /data/videos \
    && chown 1000:1000 /data/audio /models
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
USER worker
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
