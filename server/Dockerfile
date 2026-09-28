# Образ сервера. Базовый образ закреплён по контрольной сумме (digest); зависимости — по хэшам (requirements.lock).
FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY server/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock
COPY server/app ./app
COPY server/alembic.ini ./alembic.ini
COPY server/migrations ./migrations
COPY web ./web
ENV SIMPAD_UI_DIR=/srv/web
RUN useradd -r -u 10001 simpad && chown -R root:root /srv && chmod -R a+rX /srv
USER simpad
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# Сначала миграции схемы, затем сервер. Заголовкам X-Forwarded-* не доверяем: они серверу не нужны.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:get_app --factory --host 0.0.0.0 --port 8000 --no-server-header"]
