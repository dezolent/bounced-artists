from catalog.admin import register_domain
register_domain("rights")
from django.contrib import admin
from django.apps import apps

class ContributorInline(admin.TabularInline):
    model = apps.get_model("rights", "WorkContributor")
    extra = 0
    autocomplete_fields = ["party"]

admin.site._registry[apps.get_model("rights", "MusicalWork")].inlines = [ContributorInline]
