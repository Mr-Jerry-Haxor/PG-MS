from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('finance', '0014_add_booking_to_payment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentChangeLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('changed_at', models.DateTimeField(auto_now_add=True)),
                ('changes', models.JSONField(default=dict)),
                ('payment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='change_logs', to='finance.payment')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payment_change_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-changed_at', '-id')},
        ),
        migrations.AddField(
            model_name='payment',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_payments', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='expenditure',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_expenditures', to=settings.AUTH_USER_MODEL),
        ),
    ]
