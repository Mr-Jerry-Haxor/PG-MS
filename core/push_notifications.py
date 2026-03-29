import json
import logging
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

from django.conf import settings

from .models import UserDeviceToken

logger = logging.getLogger(__name__)

_firebase_app = None
_FCM_MAX_TOKENS_PER_BATCH = 500


def _is_push_enabled() -> bool:
    return bool(getattr(settings, 'FIREBASE_PUSH_ENABLED', False))


def _resolve_service_account_file() -> str:
    """Resolve service-account file from configured value or common fallbacks."""
    configured = (getattr(settings, 'FIREBASE_SERVICE_ACCOUNT_FILE', '') or '').strip()
    base_dir = Path(getattr(settings, 'BASE_DIR', Path.cwd()))

    candidates = []
    if configured:
        candidates.append(Path(configured))
        candidates.append(base_dir / configured)

    # Friendly fallback for common naming used by teams.
    candidates.append(base_dir / 'firebase-admin-sdk.json')

    # Avoid duplicate checks while preserving order.
    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            return str(path)

    return ''


def _absolute_link(url: str) -> str:
    """FCM webpush link should be absolute HTTPS (localhost allowed for dev)."""
    raw = (url or '/').strip() or '/'
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw

    base_url = (getattr(settings, 'APP_BASE_URL', '') or '').strip() or 'http://127.0.0.1:8000'
    if not base_url.endswith('/'):
        base_url += '/'
    return urljoin(base_url, raw.lstrip('/'))


def _load_firebase():
    """Lazy-load firebase-admin and initialize app once."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    if not _is_push_enabled():
        return None

    credentials_file = _resolve_service_account_file()
    if not credentials_file:
        logger.info('FCM disabled: Firebase service-account file could not be resolved.')
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials
    except Exception as exc:
        logger.warning('FCM disabled: firebase_admin import failed: %s', exc)
        return None

    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(credentials_file)
            _firebase_app = firebase_admin.initialize_app(cred)
        else:
            _firebase_app = firebase_admin.get_app()
        return _firebase_app
    except Exception as exc:
        logger.warning('FCM disabled: Firebase app initialization failed: %s', exc)
        return None


def send_push_to_tokens(tokens: Iterable[str], title: str, body: str, url: str = '/', extra_data: Optional[dict] = None):
    app = _load_firebase()
    if not app:
        return {'sent': 0, 'failed': 0}

    tokens = [t for t in set(tokens) if t]
    if not tokens:
        return {'sent': 0, 'failed': 0}

    try:
        from firebase_admin import messaging
    except Exception as exc:
        logger.warning('FCM disabled while sending: %s', exc)
        return {'sent': 0, 'failed': len(tokens)}

    data = {'url': url or '/'}
    webpush_link = _absolute_link(url or '/')
    if extra_data:
        for key, value in extra_data.items():
            if value is None:
                continue
            data[str(key)] = value if isinstance(value, str) else json.dumps(value, default=str)

    webpush_kwargs = {}
    if webpush_link.startswith('https://'):
        webpush_kwargs['fcm_options'] = messaging.WebpushFCMOptions(link=webpush_link)
    else:
        logger.info('Skipping webpush link because it is not HTTPS: %s', webpush_link)

    total_sent = 0
    total_failed = 0
    invalid_tokens = []

    for start in range(0, len(tokens), _FCM_MAX_TOKENS_PER_BATCH):
        batch_tokens = tokens[start:start + _FCM_MAX_TOKENS_PER_BATCH]
        message = messaging.MulticastMessage(
            tokens=batch_tokens,
            notification=messaging.Notification(title=title, body=body),
            data=data,
            webpush=messaging.WebpushConfig(**webpush_kwargs),
        )

        try:
            response = messaging.send_each_for_multicast(message, app=app)
        except Exception as exc:
            logger.warning('FCM send failed for batch %s-%s: %s', start, start + len(batch_tokens), exc)
            total_failed += len(batch_tokens)
            continue

        total_sent += response.success_count
        total_failed += response.failure_count

        for idx, item in enumerate(response.responses):
            if item.success:
                continue
            err = str(item.exception or '').lower()
            if (
                'registration-token-not-registered' in err
                or 'unregistered' in err
                or 'invalid-registration-token' in err
            ):
                invalid_tokens.append(batch_tokens[idx])

    if invalid_tokens:
        UserDeviceToken.objects.filter(token__in=invalid_tokens).update(is_active=False)

    return {'sent': total_sent, 'failed': total_failed}


def send_push_to_users(users: Iterable, title: str, body: str, url: str = '/', extra_data: Optional[dict] = None):
    user_ids = [getattr(u, 'id', None) for u in users]
    user_ids = [uid for uid in user_ids if uid]
    if not user_ids:
        return {'sent': 0, 'failed': 0}

    tokens = list(
        UserDeviceToken.objects.filter(user_id__in=user_ids, is_active=True)
        .values_list('token', flat=True)
    )
    return send_push_to_tokens(tokens, title=title, body=body, url=url, extra_data=extra_data)


def send_push_to_user(user, title: str, body: str, url: str = '/', extra_data: Optional[dict] = None):
    if not user:
        return {'sent': 0, 'failed': 0}
    return send_push_to_users([user], title=title, body=body, url=url, extra_data=extra_data)
