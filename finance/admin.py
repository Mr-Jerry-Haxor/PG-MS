from django.contrib import admin
from .models import Fees, Payment, Expenditure, MonthlyAdjustment


@admin.register(Fees)
class FeesAdmin(admin.ModelAdmin):
	list_display = ("pg", "share_type", "monthly_fee", "advance_amount")
	list_filter = ("pg", "share_type")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
	list_display = ("user", "pg", "amount", "date", "status", "mode", "type")
	list_filter = ("pg", "status", "mode", "type")
	search_fields = ("user__email",)


@admin.register(Expenditure)
class ExpenditureAdmin(admin.ModelAdmin):
	list_display = ("pg", "category", "amount", "date")
	list_filter = ("pg", "category")


@admin.register(MonthlyAdjustment)
class MonthlyAdjustmentAdmin(admin.ModelAdmin):
	list_display = ("user", "pg", "adjustment_type", "amount", "duration_type", "is_active", "created_at")
	list_filter = ("pg", "adjustment_type", "duration_type", "is_active")
	search_fields = ("user__email", "user__first_name", "user__last_name", "notes")
	readonly_fields = ("created_at", "updated_at")

# Register your models here.
