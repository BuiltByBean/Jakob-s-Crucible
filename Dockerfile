# Explicit production image. Nixpacks kept detecting this repo as a Node app
# (package.json exists only for the local Tailwind build) even with a
# providers override — Railway's platform detection injected Node build args
# and the image shipped without Python deps ("gunicorn: command not found").
# A repo Dockerfile removes all detection: Python, requirements, gunicorn.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first for layer caching — code edits don't re-install pips.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Shell form ON PURPOSE: $PORT must expand at runtime. This CMD is the ONLY
# start command — railway.json must NOT set deploy.startCommand, because
# Railway execs that override without a shell on Dockerfile builds and
# gunicorn receives the literal string "$PORT" (failed deploy 2026-08-18).
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 8 --timeout 60 --graceful-timeout 10 --keep-alive 2
