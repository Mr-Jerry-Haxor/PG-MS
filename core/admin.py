from django.contrib import admin
from .models import Notification, AuditLog, UserDeviceToken


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
	list_display = ("user", "title", "is_read", "created_at")


@admin.register(UserDeviceToken)
class UserDeviceTokenAdmin(admin.ModelAdmin):
	list_display = ("user", "device_type", "is_active", "last_seen_at", "created_at")
	search_fields = ("user__email", "token", "user_agent")
	list_filter = ("device_type", "is_active")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
	list_display = ("created_at", "actor", "action", "target_type", "target_id")
	search_fields = ("action", "target_type", "message")
	list_filter = ("action",)

# Register your models here.
