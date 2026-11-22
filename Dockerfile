# Single image for both Fly process groups (web + worker).
# Building once guarantees the API and worker can never run mismatched code.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq is needed by asyncpg at build time; nothing else is.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api/app ./app

# Run as a non-root user: a worker that parses untrusted PDFs should not own the
# filesystem it runs on.
RUN useradd --create-home --uid 10001 nexus \
    && chown -R nexus:nexus /app
USER nexus

ENV PORT=8000
EXPOSE 8000

# Both process groups override this:
#   web    → uvicorn app.main:app --host 0.0.0.0 --port 8000
#   worker → python -m app.workers.main
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
