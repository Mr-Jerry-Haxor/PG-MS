from django.db import models
from django.contrib.auth import get_user_model
from core.models import TimeStampedModel


class PG(TimeStampedModel):
	name = models.CharField(max_length=200)
	address = models.TextField()
	created_by_admin = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='created_pgs')

	def __str__(self):
		return self.name


class PGAdmin(TimeStampedModel):
	# Allow a single user to manage multiple PGs
	user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='pg_admin_profile')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='admins')

	def __str__(self):
		return f"{self.user} ({self.pg})"

# Create your models here.
