FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg ca-certificates tini tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-runtime.lock /app/requirements-runtime.lock
RUN python -m pip install --no-cache-dir -r requirements-runtime.lock
COPY main.py alembic.ini /app/
COPY migrations /app/migrations
COPY modules /app/modules
COPY deploy /app/deploy
RUN chmod 755 /app/deploy/ollama && ln -s /app/deploy/ollama /usr/local/bin/ollama \
    && mkdir -p config data site audio_queue music background_music tmp logs \
    && ln -s /app/logs/nova.log /app/nova.log
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "deploy.entrypoint"]
CMD ["web"]
