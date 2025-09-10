from django.contrib import admin
from .models import PG, PGAdmin


@admin.register(PG)
class PGAdminAdmin(admin.ModelAdmin):
	list_display = ("name", "address", "created_at")
	search_fields = ("name", "address")


@admin.register(PGAdmin)
class PGAdminProfileAdmin(admin.ModelAdmin):
	list_display = ("user", "pg", "created_at")
	search_fields = ("user__email", "pg__name")

# Register your models here.
