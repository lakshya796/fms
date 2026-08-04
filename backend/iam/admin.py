from django.contrib import admin
from .models import AuditLog, Branch, Organisation, Role, UserProfile

admin.site.register([Organisation, Branch, Role, UserProfile, AuditLog])
