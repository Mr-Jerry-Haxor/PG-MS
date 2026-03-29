from pgadmin.models import PG


def unread_counts(request):
    if request.user.is_authenticated and hasattr(request.user, 'notifications'):
        return {"unread_notifications": request.user.notifications.filter(is_read=False).count()}
    return {"unread_notifications": 0}


def pg_context(request):
    """Provide PG list and active PG for PG Admin users globally for templates.
    Includes superusers and website admins, who see all PGs.
    """
    try:
        user = request.user
        if not getattr(user, 'is_authenticated', False):
            return {}
        profile = getattr(user, 'profile', None)
        is_site_admin = bool(profile and getattr(profile, 'is_website_admin', False))
        is_pg_admin = bool(profile and getattr(profile, 'is_pg_admin', False))
        if not (getattr(user, 'is_superuser', False) or is_site_admin or is_pg_admin):
            return {}
        if getattr(user, 'is_superuser', False) or is_site_admin:
            pgs_qs = PG.objects.all().order_by('name')
        else:
            pgs_qs = PG.objects.filter(admins__user=user).order_by('name')
        pgs = list(pgs_qs)
        active_pg = None
        active_pg_id = request.session.get('active_pg_id')
        if active_pg_id:
            active_pg = next((p for p in pgs if p.id == active_pg_id), None)
        if not active_pg and pgs:
            active_pg = pgs[0]
        return {"pgs": pgs, "pg": active_pg}
    except Exception:
        # Fail-quietly to avoid breaking templates
        return {}


def google_client(request):
    from django.conf import settings
    return {"GOOGLE_CLIENT_ID": getattr(settings, 'SOCIALACCOUNT_PROVIDERS', {}).get('google', {}).get('APP', {}).get('client_id', '')}


def firebase_config(request):
    from django.conf import settings
    return {
        'FIREBASE_PUSH_ENABLED': bool(getattr(settings, 'FIREBASE_PUSH_ENABLED', False)),
        'FIREBASE_VAPID_KEY': getattr(settings, 'FIREBASE_VAPID_KEY', ''),
        'FIREBASE_WEB_CONFIG': {
            'apiKey': getattr(settings, 'FIREBASE_WEB_API_KEY', ''),
            'authDomain': getattr(settings, 'FIREBASE_WEB_AUTH_DOMAIN', ''),
            'projectId': getattr(settings, 'FIREBASE_WEB_PROJECT_ID', ''),
            'storageBucket': getattr(settings, 'FIREBASE_WEB_STORAGE_BUCKET', ''),
            'messagingSenderId': getattr(settings, 'FIREBASE_WEB_MESSAGING_SENDER_ID', ''),
            'appId': getattr(settings, 'FIREBASE_WEB_APP_ID', ''),
        },
    }
