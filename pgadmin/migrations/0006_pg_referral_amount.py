from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pgadmin', '0005_pg_slug'),
    ]

    operations = [
        migrations.AddField(
            model_name='pg',
            name='referral_amount',
            field=models.DecimalField(default=0, decimal_places=2, max_digits=10, help_text='Default referral credit amount for this PG.'),
        ),
    ]
