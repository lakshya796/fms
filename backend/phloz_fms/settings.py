import os
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent.parent
SECRET_KEY=os.environ["DJANGO_SECRET_KEY"]
DEBUG=os.getenv("DJANGO_DEBUG","false").lower()=="true"
ALLOWED_HOSTS=[x.strip() for x in os.environ["DJANGO_ALLOWED_HOSTS"].split(",") if x.strip()]
INSTALLED_APPS=["django.contrib.admin","django.contrib.auth","django.contrib.contenttypes","django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles","corsheaders","rest_framework","rest_framework.authtoken","fleet"]
MIDDLEWARE=["django.middleware.security.SecurityMiddleware","whitenoise.middleware.WhiteNoiseMiddleware","corsheaders.middleware.CorsMiddleware","django.contrib.sessions.middleware.SessionMiddleware","django.middleware.common.CommonMiddleware","django.middleware.csrf.CsrfViewMiddleware","django.contrib.auth.middleware.AuthenticationMiddleware","django.contrib.messages.middleware.MessageMiddleware"]
ROOT_URLCONF="phloz_fms.urls"
TEMPLATES=[{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION="phloz_fms.wsgi.application"
DATABASES={"default":{"ENGINE":"django.db.backends.sqlite3","NAME":BASE_DIR/"db.sqlite3"}} if os.getenv("USE_SQLITE","false").lower()=="true" else {"default":{"ENGINE":"django.db.backends.postgresql","NAME":os.environ["POSTGRES_DB"],"USER":os.environ["POSTGRES_USER"],"PASSWORD":os.environ["POSTGRES_PASSWORD"],"HOST":os.environ["POSTGRES_HOST"],"PORT":os.getenv("POSTGRES_PORT","5432")}}
AUTH_PASSWORD_VALIDATORS=[]
LANGUAGE_CODE="en-in"; TIME_ZONE="Asia/Kolkata"; USE_I18N=True; USE_TZ=True
STATIC_URL="/static/"; STATIC_ROOT=BASE_DIR/"staticfiles"; DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"
CORS_ALLOWED_ORIGINS=[x.strip() for x in os.environ["CORS_ALLOWED_ORIGINS"].split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS=CORS_ALLOWED_ORIGINS
SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https")
SESSION_COOKIE_SECURE=True; CSRF_COOKIE_SECURE=True
REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES":["rest_framework.authentication.TokenAuthentication","rest_framework.authentication.SessionAuthentication"],"DEFAULT_PERMISSION_CLASSES":["rest_framework.permissions.IsAuthenticated"],"DEFAULT_PAGINATION_CLASS":"fleet.pagination.StandardPagination","PAGE_SIZE":50}
