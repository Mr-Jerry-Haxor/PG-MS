from django.contrib.auth.models import AnonymousUser


def is_active_profile(user) -> bool:
    return hasattr(user, 'profile') and getattr(user.profile, 'status', 'active') == 'active'


def is_site_admin(user) -> bool:
    return is_active_profile(user) and getattr(user.profile, 'is_website_admin', False)


def is_pg_admin(user) -> bool:
    return is_active_profile(user) and getattr(user.profile, 'is_pg_admin', False)
