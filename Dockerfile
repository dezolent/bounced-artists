FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt
COPY . .
RUN DJANGO_SETTINGS_MODULE=config.settings.build python manage.py collectstatic --noinput \
    && useradd --create-home --uid 10001 app
USER app
ENV DJANGO_SETTINGS_MODULE=config.settings.production
CMD ["sh", "scripts/start.sh"]
