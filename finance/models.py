from django.db import models
from django.conf import settings
from core.models import TimeStampedModel
from pgadmin.models import PG
from django.utils import timezone


class ExpenditureCategory(TimeStampedModel):
	"""Custom expenditure categories per PG."""
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='expenditure_categories')
	name = models.CharField(max_length=100)
	slug = models.SlugField(max_length=100)
	is_default = models.BooleanField(default=False, help_text='Default categories cannot be deleted')
	display_order = models.IntegerField(default=0)

	class Meta:
		unique_together = ('pg', 'slug')
		ordering = ['display_order', 'name']

	def __str__(self):
		return f"{self.pg.name} - {self.name}"


class Fees(TimeStampedModel):
	SHARE_TYPES = [
		('1', 'Single'),
		('2', '2-Sharing'),
		('3', '3-Sharing'),
		('4', '4-Sharing'),
		('5', '5-Sharing'),
		('6', '6-Sharing'),
		('7', '7-Sharing'),
		('8', '8-Sharing'),
		('9', '9-Sharing'),
		('10', '10-Sharing'),
	]
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='fees')
	share_type = models.CharField(max_length=2, choices=SHARE_TYPES)
	monthly_fee = models.DecimalField(max_digits=10, decimal_places=2)
	advance_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

	class Meta:
		unique_together = ('pg', 'share_type')

	def __str__(self):
		return f"{self.pg} - {self.get_share_type_display()}"


class Payment(TimeStampedModel):
	STATUS_CHOICES = [('success', 'Success'), ('failed', 'Failed'), ('pending', 'Pending')]
	MODE_CHOICES = [('cash', 'Cash'), ('upi', 'UPI'), ('upi_cash', 'UPI+CASH')]
	TYPE_CHOICES = [('fee', 'Fee'), ('advance', 'Advance'), ('daywise', 'Day-wise')]

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payments')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='payments')
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	date = models.DateField(help_text="This date is referred to as the payment date or transaction date.")
	status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='success')
	mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='upi')
	type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='fee')
	notes = models.TextField(blank=True)
	from_date = models.DateField(null=True, blank=True, help_text='Billing period start date')
	to_date = models.DateField(null=True, blank=True, help_text='Billing period end date')
	upi_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='UPI component when mode is UPI+CASH')
	cash_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Cash component when mode is UPI+CASH')

	def __str__(self):
		return f"{self.user} - {self.pg} - {self.amount}"


class Expenditure(TimeStampedModel):
	CATEGORY_CHOICES = [
		('electricity', 'Electricity'),
		('groceries', 'Groceries'),
		('maintenance', 'Maintenance'),
		('advance_return', 'Advance Return'),
		('rent', 'Rent'),
		('vegetables', 'Vegetables'),
		('gas_bill', 'Gas Bill'),
		('drinking_water_bill', 'Drinking Water Bill'),
		('municipal_water_bill', 'Municipal Water Bill'),
		('milk', 'Milk'),
		('chicken', 'Chicken'),
		('paneer', 'Paneer'),
		('other', 'Other'),
	]
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='expenditures')
	# Legacy category field (kept for backward compatibility)
	category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, blank=True, null=True)
	# New custom category reference
	category_custom = models.ForeignKey(ExpenditureCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenditures')
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	date = models.DateField()
	notes = models.TextField(blank=True)
	# Optional reference to booking for advance returns (nullable to keep expenditure even if booking deleted)
	booking = models.ForeignKey('bookings.Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenditures')

	def get_category_display(self):
		"""Return the display name for the category (custom or legacy)."""
		if self.category_custom:
			return self.category_custom.name
		elif self.category:
			return dict(self.CATEGORY_CHOICES).get(self.category, self.category)
		return 'Uncategorized'

	def __str__(self):
		return f"{self.pg} - {self.get_category_display()} - {self.amount}"

# Create your models here.


class ResidentRate(TimeStampedModel):
	"""Optional per-resident custom monthly rent override for a PG.
	If not present, expected monthly rent is derived from PG Fees by share type.
	"""
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='resident_rates')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='resident_rates')
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	active = models.BooleanField(default=True)
	notes = models.CharField(max_length=255, blank=True)

	class Meta:
		unique_together = ('user', 'pg')

	def __str__(self):
		return f"{self.user} @ {self.pg}: {self.amount}"


class ReminderLog(TimeStampedModel):
	"""Logs reminders sent to residents for unpaid dues."""
	METHOD_CHOICES = [('email', 'Email'), ('whatsapp', 'WhatsApp'), ('both', 'Both')]
	by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='reminders_sent')
	to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reminders_received')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='reminder_logs')
	method = models.CharField(max_length=16, choices=METHOD_CHOICES)
	subject = models.CharField(max_length=200, blank=True)
	message = models.TextField(blank=True)
	for_month = models.DateField(help_text='Any date within the target month')

	def __str__(self):
		return f"Reminder to {self.to_user} ({self.method}) for {self.for_month:%Y-%m}"


class Adjustment(TimeStampedModel):
	"""Ledger adjustments: debit/credit and deposit deductions."""
	TYPE_CHOICES = [
		('credit', 'Credit'),
		('debit', 'Debit'),
		('deposit_deduction', 'Deposit Deduction'),
	]
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='adjustments')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='adjustments')
	type = models.CharField(max_length=32, choices=TYPE_CHOICES)
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	date = models.DateField(default=timezone.now)
	notes = models.TextField(blank=True)

	def __str__(self):
		return f"{self.user} {self.type} {self.amount}"


class MonthlyAdjustment(TimeStampedModel):
	"""Discount or increment to monthly fees for specific residents."""
	ADJUSTMENT_TYPE_CHOICES = [
		('discount', 'Discount'),
		('increment', 'Increment'),
	]
	DURATION_TYPE_CHOICES = [
		('continuous', 'Continuous'),
		('one_month', 'One Month'),
		('multiple_months', 'Multiple Months'),
	]
	
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='monthly_adjustments')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='monthly_adjustments')
	adjustment_type = models.CharField(max_length=16, choices=ADJUSTMENT_TYPE_CHOICES)
	amount = models.DecimalField(max_digits=10, decimal_places=2, help_text='Amount to discount or increment')
	duration_type = models.CharField(max_length=16, choices=DURATION_TYPE_CHOICES)
	selected_months = models.JSONField(default=list, blank=True, help_text='List of YYYY-MM strings for specific months')
	is_active = models.BooleanField(default=True)
	created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_monthly_adjustments')
	notes = models.TextField(blank=True)
	
	class Meta:
		ordering = ['-created_at']
	
	def __str__(self):
		return f"{self.user} - {self.adjustment_type} ₹{self.amount} ({self.duration_type})"
	
	def applies_to_month(self, year_month_str):
		"""Check if this adjustment applies to a given month (YYYY-MM format)."""
		if not self.is_active:
			return False
		if self.duration_type == 'continuous':
			return True
		# For one_month or multiple_months, check if the month is in selected_months
		return year_month_str in (self.selected_months or [])
	
	def get_adjusted_amount(self, base_amount, year_month_str):
		"""Return the adjusted amount for a given base amount and month."""
		if not self.applies_to_month(year_month_str):
			return base_amount
		if self.adjustment_type == 'discount':
			return max(0, base_amount - self.amount)
		else:  # increment
			return base_amount + self.amount
