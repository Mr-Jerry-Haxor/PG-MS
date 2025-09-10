from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0010_alter_booking_unique_together_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='roomsharestatus',
            name='vacant_from',
            field=models.DateField(null=True, blank=True, help_text='Date from which the share will become vacant (post confirmed leaving).'),
        ),
        migrations.AlterField(
            model_name='roomsharestatus',
            name='status',
            field=models.CharField(choices=[('vacant', 'Vacant'), ('reserved', 'Reserved'), ('occupied', 'Occupied'), ('vacant_from', 'Vacant From')], default='vacant', max_length=10),
        ),
    ]
