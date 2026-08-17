"""Settings for the standalone MAIR Voucher Portal API.

This is the voucher portal on its own - no fleet, accounting or IAM apps. It
carries only what the portal needs: Django auth (for logins and token auth),
DRF, CORS, and the `voucher_portal` app itself.

Everything environment-specific comes from the environment, because the same
image runs in every environment on ECS and a container must never carry a
baked-in secret. Only DJANGO_SECRET_KEY is mandatory; the rest have defaults
that are safe for local work and wrong for production, so the deployment sets
them explicitly (see deploy/ecs/task-definition.json).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_bool(name, default="false"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = env_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "voucher_portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Above CommonMiddleware, or a cross-origin request is answered before the
    # CORS headers are attached and the browser drops a perfectly good response.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

# Postgres in every real environment. USE_SQLITE is for a quick local run only:
# SQLite serialises writes at the table level, so it cannot honour the row lock
# the voucher numbering allocator depends on under real concurrency.
if env_bool("USE_SQLITE"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Asia/Dubai")
USE_I18N = True
USE_TZ = True

# Set when a reverse proxy mounts the API under a path prefix (e.g. /api).
# Without it Django generates every link, form action and stylesheet URL
# against the domain root, and the admin loads unstyled with a login form that
# posts nowhere.
FORCE_SCRIPT_NAME = os.getenv("DJANGO_SCRIPT_NAME") or None

STATIC_URL = f"{FORCE_SCRIPT_NAME or ''}/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Hashed filenames need a manifest, which only exists once collectstatic has
# run - so it is opt-in and the image turns it on after collecting (see the
# Dockerfile). Left on by default it breaks `manage.py test` and any local run
# that hasn't collected, with a "Missing staticfiles manifest entry" 500.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if env_bool("DJANGO_STATIC_MANIFEST") else
                    "whitenoise.storage.CompressedStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploaded card artwork. On ECS this must be a mounted volume (EFS) or S3 -
# a container filesystem is thrown away on every deploy, and artwork stored
# there disappears with it. See deploy/ecs/README.md.
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "media"))

# Which browser origins may call this API. An origin that isn't listed fails in
# the browser as a CORS error with a perfectly healthy API behind it, so the
# regex form exists for hosts that give every branch its own subdomain.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
CORS_ALLOWED_ORIGIN_REGEXES = env_list("CORS_ALLOWED_ORIGIN_REGEXES")
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# ECS sits behind an ALB that terminates TLS, so Django has to be told the
# original scheme or it will build http:// URLs and refuse secure cookies.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# Waits for the portal's background PDF threads before dropping the test
# database. Postgres refuses to drop one that still has sessions on it, so
# without this a fully green run still exits non-zero under CI.
TEST_RUNNER = "config.test_runner.BackgroundAwareRunner"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
}
