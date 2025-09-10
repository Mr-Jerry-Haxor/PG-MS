from django.db import models
from django.contrib.auth import get_user_model
from core.models import TimeStampedModel


class Profile(TimeStampedModel):
	user = models.OneToOneField(get_user_model(), on_delete=models.CASCADE, related_name='profile')
	phone = models.CharField(max_length=20, blank=True)
	selfie_url = models.URLField(blank=True)
	aadhaar_number = models.CharField(max_length=20, blank=True)
	aadhaar_file_url = models.URLField(blank=True)
	STATUS_CHOICES = [("active", "Active"), ("inactive", "Inactive")]
	status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")
	is_website_admin = models.BooleanField(default=False)
	is_pg_admin = models.BooleanField(default=False)
	is_pg_user = models.BooleanField(default=True)

	def __str__(self):
		return f"Profile: {self.user}"

# Create your models here.
