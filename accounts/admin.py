from django.contrib import admin
from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
	list_display = (
		'user', 'get_email', 'phone', 'status',
		'is_website_admin', 'is_pg_admin', 'is_pg_user', 'created_at'
	)
	list_filter = (
		'status', 'is_website_admin', 'is_pg_admin', 'is_pg_user'
	)
	search_fields = (
		'user__username', 'user__email', 'phone', 'aadhaar_number'
	)
	readonly_fields = ('created_at', 'updated_at')

	def get_email(self, obj):
		return obj.user.email
	get_email.short_description = 'Email'
