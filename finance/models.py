from django.db import models
from django.conf import settings
from core.models import TimeStampedModel
from pgadmin.models import PG


class Fees(TimeStampedModel):
	SHARE_TYPES = [
		('1', 'Single'),
		('2', '2-Sharing'),
		('3', '3-Sharing'),
		('4', '4-Sharing'),
		('5', '5-Sharing'),
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
	MODE_CHOICES = [('cash', 'Cash'), ('upi', 'UPI'), ('bank', 'Bank Transfer')]
	TYPE_CHOICES = [('fee', 'Fee'), ('advance', 'Advance')]

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payments')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='payments')
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	date = models.DateField()
	status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='success')
	mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='upi')
	type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='fee')
	notes = models.TextField(blank=True)

	def __str__(self):
		return f"{self.user} - {self.pg} - {self.amount}"


class Expenditure(TimeStampedModel):
	CATEGORY_CHOICES = [
		('electricity', 'Electricity'),
		('groceries', 'Groceries'),
		('maintenance', 'Maintenance'),
		('other', 'Other'),
	]
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='expenditures')
	category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	date = models.DateField()
	notes = models.TextField(blank=True)

	def __str__(self):
		return f"{self.pg} - {self.category} - {self.amount}"

# Create your models here.
