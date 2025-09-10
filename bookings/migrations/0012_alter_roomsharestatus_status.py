from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0011_roomsharestatus_vacant_from'),
    ]

    operations = [
        migrations.AlterField(
            model_name='roomsharestatus',
            name='status',
            field=models.CharField(choices=[('vacant', 'Vacant'), ('reserved', 'Reserved'), ('occupied', 'Occupied'), ('vacant_from', 'Vacant From')], default='vacant', max_length=12),
        ),
    ]
