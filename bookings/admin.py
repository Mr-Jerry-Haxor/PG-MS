from django.contrib import admin
from .models import Room, RoomShareStatus, Booking, ResidentApplication


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
	list_display = ("pg", "room_no", "total_shares", "created_at")
	list_filter = ("pg",)
	search_fields = ("room_no", "pg__name")


@admin.register(RoomShareStatus)
class RoomShareStatusAdmin(admin.ModelAdmin):
	list_display = ("room", "share_no", "status", "updated_at")
	list_filter = ("status", "room__pg")
	search_fields = ("room__room_no",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
	list_display = ("user", "room", "share_no", "status", "start_date", "leaving_date")
	list_filter = ("status", "room__pg")
	search_fields = ("user__email", "room__room_no")


@admin.register(ResidentApplication)
class ResidentApplicationAdmin(admin.ModelAdmin):
	list_display = ("id", "user", "pg", "room", "phone", "date_of_admission", "has_vehicle", "created_at")
	list_filter = ("pg", "room__pg", "has_vehicle", "occupation", "marital_status", "food_pref")
	search_fields = ("user__email", "phone", "name", "vehicle_number")
	readonly_fields = ("selfie_url", "aadhaar_file_url", "created_at", "updated_at")
	autocomplete_fields = ("user", "booking", "pg", "room")
	fieldsets = (
		(None, {"fields": ("user", "booking", "pg", "room", "name", "dob", "age", "phone", "email")}),
		("Parents", {"fields": ("father_name", "father_phone", "mother_name", "mother_phone")}),
		("Organization / Education", {"fields": ("education", "occupation", "org_name", "org_address")}),
		("Admission", {"fields": ("date_of_admission", "food_pref", "marital_status")}),
		("Documents", {"fields": ("aadhaar_number", "selfie_url", "aadhaar_file_url")}),
		("Vehicle", {"fields": ("has_vehicle", "vehicle_number", "vehicle_model")}),
		("Declarations", {"fields": ("decl_valuables", "decl_notice", "decl_deposit", "decl_truth")}),
		("Timestamps", {"fields": ("created_at", "updated_at")}),
	)
	def get_queryset(self, request):
		qs = super().get_queryset(request)
		return qs.select_related('user', 'pg', 'room', 'booking')

# Register your models here.
