from django.contrib import admin
from .models import CatalogReviewRequest, EditorialChange


@admin.register(CatalogReviewRequest)
class CatalogReviewRequestAdmin(admin.ModelAdmin):
    list_display=['artist','status','created_at']
    list_filter=['status']
    readonly_fields=['artist','requested_by','created_at']

    def has_add_permission(self,request):
        return False


@admin.register(EditorialChange)
class EditorialChangeAdmin(admin.ModelAdmin):
    list_display=['artist','action','model_label','created_at']
    readonly_fields=[field.name for field in EditorialChange._meta.fields]

    def has_add_permission(self,request):
        return False

    has_change_permission=has_add_permission
    has_delete_permission=has_add_permission
