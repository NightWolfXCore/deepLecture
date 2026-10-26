FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /repo
COPY tests/backend/requirements.txt /tmp/test-requirements.txt
COPY backend/python/requirements.txt /tmp/app-requirements.txt
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then cat /run/secrets/proxy_ca >> /etc/ssl/certs/ca-certificates.crt; fi; \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt pip install --no-cache-dir -r /tmp/test-requirements.txt -r /tmp/app-requirements.txt
COPY backend/python /repo/backend/python
COPY tests /repo/tests
ENV PYTHONPATH=/repo/backend/python:/repo/tests/backend
CMD ["python", "-m", "pytest", "-q", "tests/backend/python", "tests/backend/http"]
