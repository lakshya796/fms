import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "phloz_fms.settings")
application = get_wsgi_application()
