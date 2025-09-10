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
