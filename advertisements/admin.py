from django.contrib import admin
from .models import AdvertisementSettings, AdvertisementImage, AdvertisementText


@admin.register(AdvertisementSettings)
class AdvertisementSettingsAdmin(admin.ModelAdmin):
    list_display = ['pg', 'carousel_enabled', 'text_enabled', 'carousel_interval', 'updated_at']
    list_filter = ['carousel_enabled', 'text_enabled']
    search_fields = ['pg__name']


@admin.register(AdvertisementImage)
class AdvertisementImageAdmin(admin.ModelAdmin):
    list_display = ['pg', 'title', 'order', 'is_active', 'created_at']
    list_filter = ['pg', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['pg__name', 'title']
    ordering = ['pg', 'order']


@admin.register(AdvertisementText)
class AdvertisementTextAdmin(admin.ModelAdmin):
    list_display = ['pg', 'text_preview', 'order', 'is_active', 'created_at']
    list_filter = ['pg', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['pg__name', 'text']
    ordering = ['pg', 'order']

    def text_preview(self, obj):
        return obj.text[:50] + '...' if len(obj.text) > 50 else obj.text
    text_preview.short_description = 'Text'
