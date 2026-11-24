FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=epg.docker_settings TZ=Asia/Shanghai
WORKDIR /app
COPY docker/requirements.lock /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && groupadd -g 10001 epg && useradd -u 10001 -g epg -m epg \
    && mkdir -p /data /app/download && chown -R epg:epg /data /app
COPY --chown=epg:epg . /app
RUN DJANGO_SECRET_KEY=build-only-not-used-at-runtime EPG_DATABASE_PATH=:memory: python manage.py makemigrations --check --dry-run \
    && DJANGO_SECRET_KEY=build-only-not-used-at-runtime EPG_DATABASE_PATH=:memory: python manage.py collectstatic --noinput
# Entrypoint initializes bind-mount directories and drops to UID/GID 10001.
USER root
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=4)"
ENTRYPOINT ["python", "docker/entrypoint.py"]
CMD ["all"]
