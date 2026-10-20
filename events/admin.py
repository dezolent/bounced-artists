from catalog.admin import register_domain
register_domain("events")
from django.contrib import admin
from django.apps import apps

class EventArtistInline(admin.TabularInline):
    model = apps.get_model("events", "EventArtist")
    extra = 0
    autocomplete_fields = ["artist"]

admin.site._registry[apps.get_model("events", "Event")].inlines = [EventArtistInline]
