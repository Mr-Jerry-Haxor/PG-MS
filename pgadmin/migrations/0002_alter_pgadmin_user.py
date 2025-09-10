from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pgadmin', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pgadmin',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pg_admin_profile', to=settings.AUTH_USER_MODEL),
        ),
        # Optional: add a unique_together to prevent duplicate assignments for same (user, pg)
        migrations.AlterUniqueTogether(
            name='pgadmin',
            unique_together={('user', 'pg')},
        ),
    ]
