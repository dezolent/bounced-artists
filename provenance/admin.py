from django.contrib import admin
from django.apps import apps
from catalog.admin import register_domain

register_domain("provenance")
for name in ("SourceRecord", "FieldObservation", "AuditEvent"):
    model = apps.get_model("provenance", name)
    model_admin = admin.site._registry[model]
    model_admin.has_change_permission = lambda request, obj=None: False
    model_admin.has_delete_permission = lambda request, obj=None: False
