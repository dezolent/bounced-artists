from django.contrib import admin
from .models import Artist, ArtistAlias, ArtistContact, ArtistMembership, ArtistProfileLink, Service, Organization, OrganizationMembership


class AliasInline(admin.TabularInline):
    model = ArtistAlias
    extra = 0


class ContactInline(admin.TabularInline):
    model = ArtistContact
    extra = 0


class ProfileLinkInline(admin.TabularInline):
    model = ArtistProfileLink
    extra = 0
    autocomplete_fields = ["platform"]


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "organization", "management_status", "location", "updated_at"]
    list_filter = ["organization", "management_status"]
    search_fields = ["name", "slug", "aliases__name"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["organization"]
    inlines = [AliasInline, ContactInline, ProfileLinkInline]


@admin.register(ArtistMembership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["artist", "user", "role", "updated_at"]
    list_filter = ["role"]
    search_fields = ["artist__name", "user__username"]
    autocomplete_fields = ["artist", "user"]
    readonly_fields = ["created_at", "updated_at"]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ArtistAlias)
class AliasAdmin(admin.ModelAdmin):
    list_display = ["name", "artist"]
    search_fields = ["name", "artist__name"]
    autocomplete_fields = ["artist"]


@admin.register(ArtistContact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ["artist", "purpose", "label", "visibility"]
    list_filter = ["purpose", "visibility"]
    search_fields = ["artist__name", "label"]
    autocomplete_fields = ["artist"]


@admin.register(ArtistProfileLink)
class ProfileLinkAdmin(admin.ModelAdmin):
    list_display = ["artist", "platform", "verification_status"]
    list_filter = ["platform", "verification_status"]
    search_fields = ["artist__name", "external_id", "url"]
    autocomplete_fields = ["artist", "platform"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    search_fields = ["name", "slug"]
    list_display = ["name", "slug"]


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    search_fields = ["organization__name", "user__username"]
    list_display = ["organization", "user", "role"]
    def has_module_permission(self, request): return request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission
