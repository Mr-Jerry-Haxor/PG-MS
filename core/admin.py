from django.contrib import admin
from .models import Notification, AuditLog


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
	list_display = ("user", "title", "is_read", "created_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
	list_display = ("created_at", "actor", "action", "target_type", "target_id")
	search_fields = ("action", "target_type", "message")
	list_filter = ("action",)

# Register your models here.
