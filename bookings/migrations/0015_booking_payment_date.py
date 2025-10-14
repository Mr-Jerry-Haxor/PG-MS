from django.db import migrations, models
from django.utils import timezone


def _default_payment_date(booking):
    anchor = booking.joining_date or booking.start_date
    if anchor:
        return anchor
    created = getattr(booking, "created_at", None)
    if created:
        if timezone.is_aware(created):
            created = timezone.localtime(created)
        return created.date()
    return None


def set_payment_dates(apps, schema_editor):
    Booking = apps.get_model('bookings', 'Booking')
    updates = []
    for booking in Booking.objects.all().iterator():
        anchor = _default_payment_date(booking)
        if anchor:
            updates.append((booking.pk, anchor))
    for pk, value in updates:
        Booking.objects.filter(pk=pk).update(payment_date=value)


def unset_payment_dates(apps, schema_editor):
    Booking = apps.get_model('bookings', 'Booking')
    Booking.objects.update(payment_date=None)


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0014_residentapplication_aadhaar_file_url_2'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='payment_date',
            field=models.DateField(blank=True, null=True, help_text='Monthly rent due date; defaults to joining date.'),
        ),
        migrations.RunPython(set_payment_dates, unset_payment_dates),
    ]
