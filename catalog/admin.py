from django.contrib import admin
from django.apps import apps


def register_domain(app_name):
    for model in apps.get_app_config(app_name).get_models():
        fields = {f.name for f in model._meta.fields}
        search = [x for x in ("name", "title", "slug", "code", "external_id", "url") if x in fields]
        filters = [x for x in ("status", "review_status", "verification_status", "visibility", "category", "role", "service_type") if x in fields]
        cls = type(f"{model.__name__}Admin", (admin.ModelAdmin,), {
            "search_fields": search or ["=id"],
            "list_filter": filters,
            "list_per_page": 50,
            "readonly_fields": [x for x in ("id", "created_at", "updated_at") if x in fields],
        })
        admin.site.register(model, cls)


register_domain("catalog")


class ImmutableAssetVersionAdmin(admin.ModelAdmin):
    list_display = ['id','asset','mime_type','byte_size','created_at']
    readonly_fields = [field.name for field in apps.get_model('catalog','AssetVersion')._meta.fields]

    def has_add_permission(self,request):
        return False

    has_change_permission = has_add_permission
    has_delete_permission = has_add_permission


admin.site.unregister(apps.get_model('catalog','AssetVersion'))
admin.site.register(apps.get_model('catalog','AssetVersion'),ImmutableAssetVersionAdmin)

class RecordingCreditInline(admin.TabularInline):
    model = apps.get_model("catalog", "RecordingArtist")
    extra = 0
    autocomplete_fields = ["artist"]


class ReleaseTrackInline(admin.TabularInline):
    model = apps.get_model("catalog", "ReleaseTrack")
    extra = 0
    autocomplete_fields = ["recording"]


class ReleaseCreditInline(admin.TabularInline):
    model = apps.get_model("catalog", "ReleaseArtist")
    extra = 0
    autocomplete_fields = ["artist"]


admin.site._registry[apps.get_model("catalog", "Recording")].inlines = [RecordingCreditInline]
admin.site._registry[apps.get_model("catalog", "Release")].inlines = [ReleaseCreditInline, ReleaseTrackInline]


class RecordingLabelInline(admin.TabularInline):
    model = apps.get_model("catalog", "RecordingLabel")
    extra = 0
    autocomplete_fields = ["label"]


class ReleaseLabelInline(admin.TabularInline):
    model = apps.get_model("catalog", "ReleaseLabel")
    extra = 0
    autocomplete_fields = ["label"]


class MediumInline(admin.TabularInline):
    model = apps.get_model("catalog", "Medium")
    extra = 0


admin.site._registry[apps.get_model("catalog", "Recording")].inlines += [RecordingLabelInline]
admin.site._registry[apps.get_model("catalog", "Release")].inlines += [ReleaseLabelInline, MediumInline]
