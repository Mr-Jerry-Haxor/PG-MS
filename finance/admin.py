from django.contrib import admin
from .models import Fees, Payment, Expenditure


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

# Register your models here.
