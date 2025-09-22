from django.db import models
from django.contrib.auth import get_user_model
from core.models import TimeStampedModel
from django.utils.text import slugify


class PG(TimeStampedModel):
	name = models.CharField(max_length=200)
	slug = models.SlugField(max_length=200, unique=True, blank=True)
	address = models.TextField()
	# Optional contact phone number for the PG (10-15 digits to allow country codes)
	phone = models.CharField(max_length=20, blank=True, help_text="Contact phone number")
	created_by_admin = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='created_pgs')

	def __str__(self):
		return self.name

	def save(self, *args, **kwargs):
		# Auto-generate a unique slug from name if missing or if name changed with empty slug
		if not self.slug and self.name:
			base = slugify(self.name) or 'pg'
			slug = base
			# Ensure uniqueness
			counter = 2
			Model = type(self)
			while Model.objects.filter(slug=slug).exclude(pk=self.pk).exists():
				slug = f"{base}-{counter}"
				counter += 1
			self.slug = slug
		super().save(*args, **kwargs)


class PGAdmin(TimeStampedModel):
	# Allow a single user to manage multiple PGs
	user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='pg_admin_profile')
	pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='admins')

	def __str__(self):
		return f"{self.user} ({self.pg})"

# Create your models here.
