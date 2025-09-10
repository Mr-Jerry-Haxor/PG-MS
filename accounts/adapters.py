from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.utils import user_email, user_username
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()

class PGAccountAdapter(DefaultAccountAdapter):
    # We could customize behavior if needed later
    pass

class PGSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Automatically link a social login (Google) to an existing user with same verified email.

    This avoids the 'account already exists' error by attaching the social account
    instead of forcing manual login then connect.
    """
    def pre_social_login(self, request, sociallogin):
        # If already linked, nothing to do
        if sociallogin.is_existing:
            return
        email = sociallogin.account.extra_data.get('email') or sociallogin.user.email
        if not email:
            return
        try:
            existing = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return
        # Link this new social account to existing user
        sociallogin.state['process'] = 'connect'
        sociallogin.connect(request, existing)
        sociallogin.user = existing  # ensure we use existing instance

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        # Ensure email is always populated
        if sociallogin.account.extra_data.get('email') and not user.email:
            user.email = sociallogin.account.extra_data['email']
        return user
