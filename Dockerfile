# syntax=docker/dockerfile:1
FROM python:3.14-slim
WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev postgresql-client ffmpeg curl

COPY requirements/base.txt requirements/base.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements/base.txt

COPY . .

RUN useradd --uid 1000 --create-home --shell /bin/bash appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "main.py"]
