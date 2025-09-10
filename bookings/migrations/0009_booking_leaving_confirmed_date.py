from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('bookings', '0008_alter_booking_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='leaving_confirmed_date',
            field=models.DateField(null=True, blank=True),
        ),
    ]
