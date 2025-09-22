from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile
from .utils import names_from_email


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
        # If user's names are blank, derive from email once at creation
        if not (instance.first_name or instance.last_name):
            first, last = names_from_email(getattr(instance, 'email', '') or '')
            changed = False
            if first and not instance.first_name:
                instance.first_name = first
                changed = True
            if last and not instance.last_name:
                instance.last_name = last
                changed = True
            if changed:
                instance.save(update_fields=['first_name', 'last_name'])