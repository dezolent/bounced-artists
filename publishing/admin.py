from catalog.admin import register_domain
register_domain("publishing")
from django.contrib import admin
from django.apps import apps

class DestinationInline(admin.TabularInline):
    model = apps.get_model("publishing", "FanlinkDestination")
    extra = 0
    autocomplete_fields = ["platform_object"]

admin.site._registry[apps.get_model("publishing", "FanlinkPage")].inlines = [DestinationInline]


class ApprovalHistoryAdmin(admin.ModelAdmin):
    """Publication is reviewed in the portal, not through editable raw JSON."""
    readonly_fields = ()

    def get_readonly_fields(self,request,obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self,request):
        return False

    has_change_permission = has_add_permission
    has_delete_permission = has_add_permission


for name in ('ApprovedCatalogRevision','WebsiteApprovedState','ApprovedArtwork'):
    model=apps.get_model('publishing',name)
    admin.site.unregister(model)
    admin.site.register(model,ApprovalHistoryAdmin)
