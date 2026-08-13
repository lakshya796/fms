# MAIR Voucher Portal API.
#
# Multi-stage so the runtime image carries no build toolchain: wheels are built
# once, then only the installed packages are copied forward.
FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Pillow and reportlab need these to build; they are not in the runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /install /usr/local
COPY . .

# Static files are baked in, so no container needs to collect them at boot and
# every replica serves byte-identical assets. The settings module demands its
# environment at import, so placeholders are supplied for this step only - they
# never reach the running container, which takes real values from the task
# definition.
RUN DJANGO_SECRET_KEY=build-time-only USE_SQLITE=true \
    DJANGO_ALLOWED_HOSTS=localhost CORS_ALLOWED_ORIGINS=http://localhost \
    python manage.py collectstatic --noinput \
    && rm -f db.sqlite3

# Turned on only after collectstatic has produced the manifest; before that it
# would 500 on every page with "Missing staticfiles manifest entry".
ENV DJANGO_STATIC_MANIFEST=true

# Non-root: ECS will happily run as root, and there is no reason to.
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /app/media && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health/ || exit 1

# Migrations run in a one-off task, not here: with more than one replica, every
# container would race to apply the same migration on deploy. See
# deploy/ecs/README.md for the migrate task.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "2", \
     "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
