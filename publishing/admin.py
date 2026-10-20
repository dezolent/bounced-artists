from catalog.admin import register_domain
register_domain("publishing")
from django.contrib import admin
from django.apps import apps

class DestinationInline(admin.TabularInline):
    model = apps.get_model("publishing", "FanlinkDestination")
    extra = 0
    autocomplete_fields = ["platform_object"]

admin.site._registry[apps.get_model("publishing", "FanlinkPage")].inlines = [DestinationInline]
