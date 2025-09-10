from django.db import migrations


def forwards(apps, schema_editor):
    Booking = apps.get_model('bookings', 'Booking')
    updated = 0
    qs = Booking.objects.filter(status='rejected').exclude(leaving_date__isnull=True)
    for b in qs.iterator():
        b.status = 'completed'
        b.save(update_fields=['status'])
        updated += 1
    if updated:
        print(f"[convert_rejected_leaving_to_completed] Updated {updated} bookings to completed")


def backwards(apps, schema_editor):
    Booking = apps.get_model('bookings', 'Booking')
    reverted = 0
    qs = Booking.objects.filter(status='completed').exclude(leaving_date__isnull=True)
    for b in qs.iterator():
        b.status = 'rejected'
        b.save(update_fields=['status'])
        reverted += 1
    if reverted:
        print(f"[convert_rejected_leaving_to_completed:reverse] Reverted {reverted} bookings back to rejected")

class Migration(migrations.Migration):
    dependencies = [
        ('bookings', '0006_merge_0005_conflicts'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
