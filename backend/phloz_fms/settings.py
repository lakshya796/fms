import os
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent.parent
SECRET_KEY=os.environ["DJANGO_SECRET_KEY"]
DEBUG=os.getenv("DJANGO_DEBUG","false").lower()=="true"
ALLOWED_HOSTS=[x.strip() for x in os.environ["DJANGO_ALLOWED_HOSTS"].split(",") if x.strip()]
INSTALLED_APPS=["django.contrib.admin","django.contrib.auth","django.contrib.contenttypes","django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles","corsheaders","rest_framework","rest_framework.authtoken","iam","accounting","fleet","vouchers","voucher_portal"]
MIDDLEWARE=["django.middleware.security.SecurityMiddleware","whitenoise.middleware.WhiteNoiseMiddleware","corsheaders.middleware.CorsMiddleware","django.contrib.sessions.middleware.SessionMiddleware","django.middleware.common.CommonMiddleware","django.middleware.csrf.CsrfViewMiddleware","django.contrib.auth.middleware.AuthenticationMiddleware","django.contrib.messages.middleware.MessageMiddleware"]
ROOT_URLCONF="phloz_fms.urls"
TEMPLATES=[{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION="phloz_fms.wsgi.application"
# Postgres is the supported production database. USE_SQLITE=true is for local work only:
# SQLite serialises writes, and double entry accounting writes several rows per event.
if os.getenv("USE_SQLITE","false").lower()=="true":
    # SQLite serialises writes at the table level (unlike Postgres row locking), so
    # concurrent writers wait rather than block-and-queue cleanly; a short default
    # busy timeout surfaces as spurious "database is locked" errors under any real
    # write concurrency (e.g. voucher_portal's numbering allocator under test).
    DATABASES={"default":{"ENGINE":"django.db.backends.sqlite3","NAME":BASE_DIR/"db.sqlite3","OPTIONS":{"timeout":20}}}
else:
    DATABASES={"default":{
        "ENGINE":"django.db.backends.postgresql",
        "NAME":os.environ["POSTGRES_DB"],"USER":os.environ["POSTGRES_USER"],
        "PASSWORD":os.environ["POSTGRES_PASSWORD"],"HOST":os.environ["POSTGRES_HOST"],
        "PORT":os.getenv("POSTGRES_PORT","5432"),
        # Keep connections open between requests rather than reconnecting on every request,
        # and check a reused connection is still alive before handing it to a view.
        "CONN_MAX_AGE":int(os.getenv("POSTGRES_CONN_MAX_AGE","60")),
        "CONN_HEALTH_CHECKS":True,
    }}
AUTH_PASSWORD_VALIDATORS=[{"NAME":"django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},{"NAME":"django.contrib.auth.password_validation.MinimumLengthValidator","OPTIONS":{"min_length":10}},{"NAME":"django.contrib.auth.password_validation.CommonPasswordValidator"},{"NAME":"django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE="en-in"; TIME_ZONE="Asia/Kolkata"; USE_I18N=True; USE_TZ=True
STATIC_URL="/static/"; STATIC_ROOT=BASE_DIR/"staticfiles"; DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"
# Local fallback for voucher-portal artwork and PDFs, used only while
# VOUCHER_PORTAL_S3_BUCKET is unset (see voucher_portal/storage.py). MEDIA_ROOT
# points at the release-independent `shared/` directory so uploads survive a deploy.
MEDIA_URL="/media/"
MEDIA_ROOT=Path(os.getenv("MEDIA_ROOT", BASE_DIR/"media"))
CORS_ALLOWED_ORIGINS=[x.strip() for x in os.environ["CORS_ALLOWED_ORIGINS"].split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS=CORS_ALLOWED_ORIGINS
SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https")
SESSION_COOKIE_SECURE=True; CSRF_COOKIE_SECURE=True
REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES":["rest_framework.authentication.TokenAuthentication","rest_framework.authentication.SessionAuthentication"],"DEFAULT_PERMISSION_CLASSES":["rest_framework.permissions.IsAuthenticated"],"DEFAULT_PAGINATION_CLASS":"fleet.pagination.StandardPagination","PAGE_SIZE":50}
