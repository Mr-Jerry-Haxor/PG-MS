from django.contrib import admin
from .models import PG, PGAdmin, PGAdminPermission, Complaint, ComplaintComment, ComplaintMedia, OldTenant


@admin.register(PG)
class PGAdminAdmin(admin.ModelAdmin):
	list_display = ("name", "address", "created_at")
	search_fields = ("name", "address")
	
	fieldsets = (
		(None, {
			"fields": ("name", "slug", "address", "phone")
		}),
		("Fees & Settings", {
			"fields": ("referral_amount", "daywise_fee", "notice_period", "past_joining_date_allowed", "allow_custom_leave_date")
		}),
		("WhatsApp Group Settings", {
			"fields": ("whatsapp_invite_link", "whatsapp_invite_message"),
			"classes": ("collapse",),
			"description": "Optional: Configure WhatsApp group invite settings for this PG."
		}),
		("Admin", {
			"fields": ("created_by_admin",),
			"classes": ("collapse",)
		}),
	)


class PGAdminPermissionInline(admin.StackedInline):
	model = PGAdminPermission
	can_delete = False
	verbose_name_plural = 'Permissions'


@admin.register(PGAdmin)
class PGAdminProfileAdmin(admin.ModelAdmin):
	list_display = ("user", "pg", "created_at")
	search_fields = ("user__email", "pg__name")
	inlines = [PGAdminPermissionInline]


@admin.register(PGAdminPermission)
class PGAdminPermissionAdmin(admin.ModelAdmin):
	list_display = ("pg_admin", "can_view_employees", "can_delete_payments", "can_edit_payments")
	list_filter = ("can_view_employees", "can_delete_payments", "can_edit_payments")
	search_fields = ("pg_admin__user__email", "pg_admin__pg__name")


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


@admin.register(ComplaintMedia)
class ComplaintMediaAdmin(admin.ModelAdmin):
	list_display = ("complaint", "media_type", "file_name", "created_at")
	list_filter = ("media_type", "created_at")
	search_fields = ("file_name", "complaint__title")
	readonly_fields = ("created_at", "updated_at")


@admin.register(OldTenant)
class OldTenantAdmin(admin.ModelAdmin):
	list_display = ("full_name", "pg", "room_no", "joining_date", "leaving_date", "archived_at")
	list_filter = ("pg", "archived_at", "occupation", "food_pref")
	search_fields = ("full_name", "email", "phone", "whatsapp_number", "room_no", "aadhaar_number")
	readonly_fields = ("archived_at", "archived_by", "stay_duration_days")
	date_hierarchy = "archived_at"
	
	fieldsets = (
		("Tenant Information", {
			"fields": ("full_name", "email", "phone", "whatsapp_number", "original_user", "original_booking_id")
		}),
		("Personal Details", {
			"fields": ("dob", "age", "marital_status", "education", "food_pref"),
			"classes": ("collapse",)
		}),
		("Family Contact", {
			"fields": ("father_name", "father_phone", "mother_name", "mother_phone", "emergency_contact"),
			"classes": ("collapse",)
		}),
		("Address & Residence", {
			"fields": ("address", "pg", "room_no", "bed_no"),
			"classes": ("collapse",)
		}),
		("Work Information", {
			"fields": ("occupation", "org_name", "org_address"),
			"classes": ("collapse",)
		}),
		("Stay Duration", {
			"fields": ("joining_date", "leaving_date", "leaving_reason", "stay_duration_days")
		}),
		("Documents & ID", {
			"fields": ("aadhaar_number", "selfie_url", "aadhaar_file_url", "aadhaar_file_url_2"),
			"classes": ("collapse",)
		}),
		("Vehicle Information", {
			"fields": ("has_vehicle", "vehicle_number", "vehicle_model"),
			"classes": ("collapse",)
		}),
		("Financial", {
			"fields": ("advance_paid", "advance_returned")
		}),
		("Archive Metadata", {
			"fields": ("archived_at", "archived_by"),
			"classes": ("collapse",)
		}),
	)

