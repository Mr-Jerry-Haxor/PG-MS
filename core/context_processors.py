from pgadmin.models import PG


def unread_counts(request):
    if request.user.is_authenticated and hasattr(request.user, 'notifications'):
        return {"unread_notifications": request.user.notifications.filter(is_read=False).count()}
    return {"unread_notifications": 0}


def pg_context(request):
    """Provide PG list and active PG for PG Admin users globally for templates."""
    try:
        user = request.user
        if not getattr(user, 'is_authenticated', False):
            return {}
        profile = getattr(user, 'profile', None)
        if not profile or not getattr(profile, 'is_pg_admin', False):
            return {}
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
