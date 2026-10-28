FROM denoland/deno:bin-2.5.6 AS deno
FROM python:3.11-slim-bookworm
COPY --from=deno /deno /usr/local/bin/deno
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/python/requirements*.txt ./
RUN --mount=type=secret,id=proxy_ca \
    PIP_CERT="$(if [ -f /run/secrets/proxy_ca ]; then echo /run/secrets/proxy_ca; else echo /etc/ssl/certs/ca-certificates.crt; fi)" \
    && export PIP_CERT \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 \
    && pip install --no-cache-dir -r requirements-ml.txt \
    && chmod -R a+rX /app \
    && useradd --uid 1000 --create-home --groups www-data worker \
    && mkdir -p /data/videos /data/audio /data/imports /data/downloads /models \
    && chmod 0755 /data /data/audio /data/imports /data/downloads \
    && chmod 2775 /data/videos \
    && chown 33:33 /data/videos \
    && chown 33:33 /data/imports \
    && chown 1000:1000 /data/audio /data/downloads /models
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
COPY backend/python/app ./app
RUN chmod -R a+rX /app
USER worker
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
