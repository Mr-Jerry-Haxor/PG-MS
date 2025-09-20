from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from accounts.models import Profile

class Command(BaseCommand):
    help = 'Promote a user to Website Admin (Site Admin). Ensures Profile exists and is active.'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email of the user to promote')
        parser.add_argument('--superuser', action='store_true', help='Also mark as Django superuser')

    def handle(self, *args, **options):
        email = options['email']
        User = get_user_model()
        user = User.objects.filter(email=email).first()
        if not user:
            raise CommandError(f"User with email {email} not found")
        # Ensure profile exists
        profile, _ = Profile.objects.get_or_create(user=user, defaults={'status': 'active'})
        if profile.status != 'active':
            profile.status = 'active'
        profile.is_website_admin = True
        profile.is_pg_user = True
        profile.save(update_fields=['status', 'is_website_admin', 'is_pg_user'])
        if options['superuser'] and not user.is_superuser:
            user.is_superuser = True
            user.is_staff = True
            user.save(update_fields=['is_superuser', 'is_staff'])
        self.stdout.write(self.style.SUCCESS(f"Promoted {email} to Website Admin" + (" and Superuser" if options['superuser'] else "")))
