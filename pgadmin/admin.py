from django.contrib import admin
from .models import PG, PGAdmin, Complaint, ComplaintComment


@admin.register(PG)
class PGAdminAdmin(admin.ModelAdmin):
	list_display = ("name", "address", "created_at")
	search_fields = ("name", "address")


@admin.register(PGAdmin)
class PGAdminProfileAdmin(admin.ModelAdmin):
	list_display = ("user", "pg", "created_at")
	search_fields = ("user__email", "pg__name")


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
	list_display = ("title", "user", "pg", "status", "priority", "category", "created_at")
	list_filter = ("status", "priority", "category", "pg", "created_at")
	search_fields = ("title", "description", "user__email", "user__first_name", "user__last_name")
	readonly_fields = ("created_at", "updated_at", "resolved_at")
	date_hierarchy = "created_at"
	
	fieldsets = (
		("Basic Information", {
			"fields": ("user", "pg", "booking", "title", "description")
		}),
		("Classification", {
			"fields": ("category", "priority", "status")
		}),
		("Resolution", {
			"fields": ("resolved_at", "resolved_by")
		}),
		("Timestamps", {
			"fields": ("created_at", "updated_at"),
			"classes": ("collapse",)
		}),
	)


@admin.register(ComplaintComment)
class ComplaintCommentAdmin(admin.ModelAdmin):
	list_display = ("complaint", "user", "is_internal", "created_at")
	list_filter = ("is_internal", "created_at")
	search_fields = ("comment", "complaint__title", "user__email")
	readonly_fields = ("created_at", "updated_at")
