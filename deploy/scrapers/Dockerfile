# Select this Dockerfile ONLY for the artist-sync cron service.
FROM python:3.12-slim-bookworm AS providers
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/scrapers/requirements.txt /tmp/provider-requirements.txt
RUN pip install --require-hashes -r /tmp/provider-requirements.txt && pip check
COPY deploy/scrapers/sources.json deploy/scrapers/fetch_sources.py /build/
RUN python /build/fetch_sources.py /build/sources.json /opt/scrapers

FROM python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt
# Keep both interpreters, stdlibs and site-packages separate. The supervisor sets
# PYTHONHOME and LD_LIBRARY_PATH only on provider children.
COPY --from=providers /usr/local /opt/provider-python
COPY --from=providers /opt/scrapers /opt/scrapers
COPY . .
RUN useradd --create-home --uid 10001 app
USER app
ENV DJANGO_SETTINGS_MODULE=config.settings.production
# No EXPOSE or public HTTP listener, web entrypoint, migrations, or hosted fallback.
CMD ["python", "scripts/self_hosted_sync.py"]
