import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key_material = getattr(settings, 'WHATSAPP_CREDENTIAL_ENCRYPTION_KEY', settings.SECRET_KEY)
    material = f"pgms-whatsapp-cloud:{key_material}".encode('utf-8')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_secret(value):
    value = (value or '').strip()
    if not value:
        return ''
    return 'enc:' + _fernet().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_secret(value):
    if not value:
        return ''
    if not value.startswith('enc:'):
        return value
    try:
        return _fernet().decrypt(value[4:].encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''
