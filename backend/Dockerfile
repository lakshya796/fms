FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Static files are collected at build time. The settings module requires these variables at
# import, so placeholders are supplied for this step only - they never reach the running
# container, which still takes its real configuration from .env.fms at runtime.
RUN DJANGO_SECRET_KEY=build-time-only DJANGO_ALLOWED_HOSTS=localhost \
    CORS_ALLOWED_ORIGINS=http://localhost USE_SQLITE=true \
    python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn","phloz_fms.wsgi:application","--bind","0.0.0.0:8000","--workers","3","--timeout","60"]
