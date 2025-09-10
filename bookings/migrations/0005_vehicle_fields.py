from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0004_residentapplication'),
    ]

    operations = [
        migrations.AddField(
            model_name='residentapplication',
            name='has_vehicle',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='residentapplication',
            name='vehicle_number',
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name='residentapplication',
            name='vehicle_model',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
