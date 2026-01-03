import os
import uuid
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator


def advertisement_image_path(instance, filename):
    """Generate unique filename for advertisement images."""
    ext = filename.split('.')[-1]
    pg_name = instance.pg.name.replace(' ', '_') if instance.pg else 'Unknown'
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    unique_id = uuid.uuid4().hex[:8]
    new_filename = f"{pg_name}_Advertisement_{timestamp}_{unique_id}.{ext}"
    return os.path.join('advertisements', new_filename)


class AdvertisementSettings(models.Model):
    """Settings for advertisement display per PG."""
    pg = models.OneToOneField(
        'pgadmin.PG',
        on_delete=models.CASCADE,
        related_name='advertisement_settings'
    )
    carousel_enabled = models.BooleanField(
        default=False,
        help_text="Enable or disable the image carousel on user dashboard"
    )
    text_enabled = models.BooleanField(
        default=False,
        help_text="Enable or disable the scrolling text on user dashboard"
    )
    carousel_interval = models.PositiveIntegerField(
        default=5000,
        validators=[MinValueValidator(1000)],
        help_text="Time between slides in milliseconds (minimum 1000ms)"
    )
    text_scroll_speed = models.PositiveIntegerField(
        default=50,
        validators=[MinValueValidator(10)],
        help_text="Scrolling text speed (higher = slower, range 10-200)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Advertisement Settings"
        verbose_name_plural = "Advertisement Settings"

    def __str__(self):
        return f"Ad Settings for {self.pg.name}"


class AdvertisementImage(models.Model):
    """Individual advertisement images for carousel."""
    pg = models.ForeignKey(
        'pgadmin.PG',
        on_delete=models.CASCADE,
        related_name='advertisement_images'
    )
    image = models.ImageField(
        upload_to=advertisement_image_path,
        help_text="Advertisement image (will be cropped/resized before upload)"
    )
    title = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional title for the image"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description displayed on the image"
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order (lower numbers appear first)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Show this image in the carousel"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'created_at']
        verbose_name = "Advertisement Image"
        verbose_name_plural = "Advertisement Images"

    def __str__(self):
        return f"Ad Image {self.order} for {self.pg.name}"

    def delete(self, *args, **kwargs):
        """Delete the image file when the model instance is deleted."""
        if self.image:
            if os.path.isfile(self.image.path):
                os.remove(self.image.path)
        super().delete(*args, **kwargs)


class AdvertisementText(models.Model):
    """Scrolling text advertisement."""
    pg = models.ForeignKey(
        'pgadmin.PG',
        on_delete=models.CASCADE,
        related_name='advertisement_texts'
    )
    text = models.TextField(
        help_text="Text to display in the scrolling marquee"
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order (lower numbers appear first)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Show this text in the marquee"
    )
    text_color = models.CharField(
        max_length=7,
        default='#ffffff',
        help_text="Text color (hex format, e.g., #ffffff)"
    )
    background_color = models.CharField(
        max_length=7,
        default='#1e3a5f',
        help_text="Background color (hex format, e.g., #1e3a5f)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'created_at']
        verbose_name = "Advertisement Text"
        verbose_name_plural = "Advertisement Texts"

    def __str__(self):
        return f"Ad Text {self.order} for {self.pg.name}: {self.text[:30]}..."
