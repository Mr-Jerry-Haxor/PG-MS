import hashlib
import hmac
import json
import re
from datetime import datetime, timezone as datetime_timezone

import requests
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from bookings.models import ResidentApplication
from .models import (
    WhatsAppCloudConfig,
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppWebhookEvent,
)
from .whatsapp_crypto import decrypt_secret


SECTION_FIELDS = {
    'monthly_dashboard': 'enable_monthly_dashboard',
    'whatsapp_messages': 'enable_whatsapp_messages',
    'leaving_page': 'enable_leaving_page',
    'compliance_page': 'enable_compliance_page',
}


class WhatsAppCloudError(Exception):
    pass


def normalize_phone(value):
    return re.sub(r'\D', '', value or '')


def cloud_config_for(pg, section=None):
    try:
        config = pg.whatsapp_cloud_config
    except WhatsAppCloudConfig.DoesNotExist:
        return None
    if not config.enabled or (section and not config.section_enabled(section)):
        return None
    return config


def _resolve_user(pg, wa_id):
    target = normalize_phone(wa_id)
    if not target:
        return None
    applications = ResidentApplication.objects.filter(pg=pg).select_related('user').only(
        'user_id', 'whatsapp_number', 'phone'
    )
    for application in applications.iterator():
        for candidate in (application.whatsapp_number, application.phone):
            normalized = normalize_phone(candidate)
            if normalized and (normalized == target or normalized[-10:] == target[-10:]):
                return application.user
    return None


def get_or_create_conversation(pg, phone, name=''):
    wa_id = normalize_phone(phone)
    if not wa_id:
        raise WhatsAppCloudError('A valid WhatsApp phone number is required.')
    contact, _ = WhatsAppContact.objects.get_or_create(
        pg=pg,
        wa_id=wa_id,
        defaults={'name': (name or '').strip(), 'user': _resolve_user(pg, wa_id)},
    )
    update_fields = []
    if name and contact.name != name.strip():
        contact.name = name.strip()
        update_fields.append('name')
    if not contact.user_id:
        user = _resolve_user(pg, wa_id)
        if user:
            contact.user = user
            update_fields.append('user')
    if update_fields:
        contact.save(update_fields=update_fields + ['updated_at'])
    conversation, _ = WhatsAppConversation.objects.get_or_create(pg=pg, contact=contact)
    return conversation


def send_cloud_message(*, pg, to, text='', section, sent_by=None, media_url='', media_type='image'):
    if section not in SECTION_FIELDS:
        raise WhatsAppCloudError('Invalid WhatsApp section.')
    config = cloud_config_for(pg, section)
    if not config:
        raise WhatsAppCloudError('Cloud API is not enabled for this PG and section.')
    access_token = decrypt_secret(config.access_token_encrypted)
    if not config.phone_number_id or not access_token:
        raise WhatsAppCloudError('Cloud API credentials are incomplete.')

    conversation = get_or_create_conversation(pg, to)
    if media_url:
        if media_type not in {'image', 'video', 'audio', 'document', 'sticker'}:
            raise WhatsAppCloudError('Unsupported WhatsApp media type.')
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': conversation.contact.wa_id,
            'type': media_type,
            media_type: {'link': media_url},
        }
        if text:
            payload[media_type]['caption'] = text
        message_type = media_type
    else:
        if not (text or '').strip():
            raise WhatsAppCloudError('Message text is required.')
        template_name = config.template_for_section(section)
        if template_name:
            payload = {
                'messaging_product': 'whatsapp',
                'to': conversation.contact.wa_id,
                'type': 'template',
                'template': {
                    'name': template_name,
                    'language': {'code': config.template_language or 'en_US'},
                    'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': text.strip()}]}],
                },
            }
            message_type = 'template'
        else:
            payload = {
                'messaging_product': 'whatsapp',
                'recipient_type': 'individual',
                'to': conversation.contact.wa_id,
                'type': 'text',
                'text': {'preview_url': False, 'body': text.strip()},
            }
            message_type = 'text'

    message = WhatsAppMessage.objects.create(
        pg=pg,
        conversation=conversation,
        direction=WhatsAppMessage.OUTBOUND,
        message_type=message_type,
        text=(text or '').strip(),
        media_url=media_url or '',
        status='pending',
        section=section,
        sent_by=sent_by,
        raw_payload={'request': payload},
    )
    try:
        response = requests.post(
            config.resolved_messages_endpoint,
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=20,
        )
        try:
            response_data = response.json()
        except ValueError:
            response_data = {'body': response.text[:2000]}
        if not response.ok:
            error = response_data.get('error', {}) if isinstance(response_data, dict) else {}
            detail = error.get('message') or f'Cloud API returned HTTP {response.status_code}.'
            message.raw_payload = {'request': payload, 'response': response_data}
            raise WhatsAppCloudError(detail)
        provider_id = ((response_data.get('messages') or [{}])[0]).get('id')
        message.provider_message_id = provider_id or None
        message.status = 'sent'
        message.raw_payload = {'request': payload, 'response': response_data}
        message.provider_timestamp = timezone.now()
        message.save(update_fields=[
            'provider_message_id', 'status', 'raw_payload', 'provider_timestamp', 'updated_at'
        ])
        conversation.last_message_at = message.provider_timestamp
        conversation.save(update_fields=['last_message_at', 'updated_at'])
        return message
    except (requests.RequestException, WhatsAppCloudError) as exc:
        message.status = 'failed'
        message.error_message = str(exc)
        message.save(update_fields=['status', 'error_message', 'raw_payload', 'updated_at'])
        if isinstance(exc, WhatsAppCloudError):
            raise
        raise WhatsAppCloudError(f'Unable to reach the Cloud API: {exc}') from exc


def verify_signature(raw_body, signature, app_secret):
    if not signature or not signature.startswith('sha256=') or not app_secret:
        return False
    expected = hmac.new(app_secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def _message_text(message):
    message_type = message.get('type', 'unknown')
    if message_type == 'text':
        return (message.get('text') or {}).get('body', '')
    if message_type == 'button':
        return (message.get('button') or {}).get('text', '')
    if message_type == 'interactive':
        interactive = message.get('interactive') or {}
        choice = interactive.get('button_reply') or interactive.get('list_reply') or {}
        return choice.get('title') or choice.get('id') or ''
    return (message.get(message_type) or {}).get('caption', '')


def _provider_datetime(value):
    try:
        return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OSError):
        return timezone.now()


@transaction.atomic
def process_webhook_payload(payload, raw_body, signature):
    digest = hashlib.sha256(raw_body).hexdigest()
    event, created = WhatsAppWebhookEvent.objects.get_or_create(
        payload_hash=digest,
        defaults={'payload': payload},
    )
    if not created and event.processed:
        return 0

    changes_to_process = []
    for entry in payload.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value') or {}
            phone_number_id = str((value.get('metadata') or {}).get('phone_number_id') or '')
            config = WhatsAppCloudConfig.objects.select_related('pg').filter(
                enabled=True, phone_number_id=phone_number_id
            ).first()
            if not config:
                continue
            app_secret = decrypt_secret(config.app_secret_encrypted)
            if not verify_signature(raw_body, signature, app_secret):
                continue
            changes_to_process.append((config, value))

    if not changes_to_process:
        event.error_message = 'No matching configuration with a valid signature.'
        event.save(update_fields=['error_message', 'updated_at'])
        raise WhatsAppCloudError(event.error_message)

    processed = 0
    for config, value in changes_to_process:
        event.pg = config.pg
        event.phone_number_id = config.phone_number_id or ''
        profile_names = {
            str(contact.get('wa_id')): (contact.get('profile') or {}).get('name', '')
            for contact in value.get('contacts', [])
        }
        for incoming in value.get('messages', []):
            provider_id = incoming.get('id')
            if provider_id and WhatsAppMessage.objects.filter(provider_message_id=provider_id).exists():
                continue
            wa_id = normalize_phone(incoming.get('from'))
            conversation = get_or_create_conversation(config.pg, wa_id, profile_names.get(wa_id, ''))
            message_type = incoming.get('type', 'unknown')
            media = incoming.get(message_type) or {}
            timestamp = _provider_datetime(incoming.get('timestamp'))
            WhatsAppMessage.objects.create(
                pg=config.pg,
                conversation=conversation,
                provider_message_id=provider_id or None,
                direction=WhatsAppMessage.INBOUND,
                message_type=message_type,
                text=_message_text(incoming),
                media_id=media.get('id', ''),
                status='received',
                provider_timestamp=timestamp,
                raw_payload=incoming,
            )
            conversation.last_message_at = timestamp
            conversation.unread_count = F('unread_count') + 1
            conversation.save(update_fields=['last_message_at', 'unread_count', 'updated_at'])
            processed += 1

        for status_item in value.get('statuses', []):
            provider_id = status_item.get('id')
            if not provider_id:
                continue
            message = WhatsAppMessage.objects.filter(
                pg=config.pg, provider_message_id=provider_id
            ).first()
            if message:
                message.status = status_item.get('status', message.status)
                errors = status_item.get('errors') or []
                if errors:
                    message.error_message = errors[0].get('title') or errors[0].get('message') or json.dumps(errors)
                message.raw_payload = {**(message.raw_payload or {}), 'status_webhook': status_item}
                message.save(update_fields=['status', 'error_message', 'raw_payload', 'updated_at'])
                processed += 1

    event.processed = True
    event.save(update_fields=['pg', 'phone_number_id', 'processed', 'updated_at'])
    return processed
