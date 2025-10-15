from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pgadmin', '0006_pg_referral_amount'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('bookings', '0015_booking_payment_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='residentapplication',
            name='referred_by_booking',
            field=models.ForeignKey(blank=True, help_text='Booking of the resident who referred this applicant (set by PG admin).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referrals_made', to='bookings.booking'),
        ),
    migrations.CreateModel(
            name='ReferralCredit',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('scheduled_month', models.DateField(blank=True, help_text='First day of month when this credit should be applied.', null=True)),
                ('redeemed_for_month', models.DateField(blank=True, help_text='First day of the month where the credit was applied.', null=True)),
                ('redeemed_on', models.DateTimeField(blank=True, null=True)),
                ('redeemed_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('application', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='referral_credit', to='bookings.residentapplication')),
                ('pg', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referral_credits', to='pgadmin.pg')),
                ('referrer_booking', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referral_credits_source', to='bookings.booking')),
                ('referrer_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referral_credits_given', to=settings.AUTH_USER_MODEL)),
                ('referred_booking', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referral_credit_target', to='bookings.booking')),
                ('referred_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referral_credits_received', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
