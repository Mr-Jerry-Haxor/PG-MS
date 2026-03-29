from django.db import models
from django.contrib.auth import get_user_model


class TimeStampedModel(models.Model):
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		abstract = True


class Notification(TimeStampedModel):
	user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='notifications')
	title = models.CharField(max_length=200)
	message = models.TextField()
	is_read = models.BooleanField(default=False)

	def __str__(self):
		return f"{self.user} - {self.title}"


class UserDeviceToken(TimeStampedModel):
	"""Stores FCM registration tokens for web devices per user."""
	WEB = 'web'
	ANDROID = 'android'
	IOS = 'ios'
	DEVICE_CHOICES = [
		(WEB, 'Web'),
		(ANDROID, 'Android'),
		(IOS, 'iOS'),
	]

	user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='device_tokens')
	token = models.CharField(max_length=512, unique=True)
	device_type = models.CharField(max_length=20, choices=DEVICE_CHOICES, default=WEB)
	user_agent = models.CharField(max_length=500, blank=True)
	is_active = models.BooleanField(default=True)
	last_seen_at = models.DateTimeField(auto_now=True)

	class Meta:
		indexes = [
			models.Index(fields=['user', 'is_active']),
			models.Index(fields=['last_seen_at']),
		]

	def __str__(self):
		return f"{self.user} ({self.device_type})"


class AuditLog(TimeStampedModel):
	actor = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
	action = models.CharField(max_length=100)
	target_type = models.CharField(max_length=50)
	target_id = models.IntegerField()
	message = models.TextField(blank=True)
	meta = models.JSONField(blank=True, null=True)

	class Meta:
		indexes = [
			models.Index(fields=["target_type", "target_id"]),
			models.Index(fields=["created_at"]),
		]

	def __str__(self):
		return f"{self.action} {self.target_type}:{self.target_id}"

# Create your models here.
