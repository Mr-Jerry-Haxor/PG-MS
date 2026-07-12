import json
import hmac
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from bookings.models import Booking, ResidentApplication
from .models import PG, PGAdmin, WhatsAppCloudConfig, WhatsAppConversation
from .whatsapp_cloud import WhatsAppCloudError, process_webhook_payload, send_cloud_message
from .whatsapp_crypto import decrypt_secret

logger = logging.getLogger(__name__)


def _active_admin_pg(request):
    allowed = PG.objects.filter(admins__user=request.user).distinct().order_by('name')
    requested_pg_id = request.GET.get('pg') or request.POST.get('pg')
    if requested_pg_id:
        pg = allowed.filter(pk=requested_pg_id).first()
        if not pg:
            return None
    else:
        session_pg_id = request.session.get('active_pg_id')
        pg = allowed.filter(pk=session_pg_id).first() if session_pg_id else allowed.first()
    if pg:
        request.session['active_pg_id'] = pg.id
    return pg


def _is_pg_admin(user):
    return bool(user.is_authenticated and PGAdmin.objects.filter(user=user).exists())


def _pg_user_contacts(pg, query=''):
    applications = ResidentApplication.objects.filter(
        pg=pg,
        booking__status__in=[Booking.PENDING, Booking.APPROVED],
    ).select_related('user').order_by('name', 'user__email')
    if query:
        applications = applications.filter(
            Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(whatsapp_number__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
        )
    contacts = []
    seen_users = set()
    for application in applications:
        phone = (application.whatsapp_number or application.phone or '').strip()
        if not phone:
            continue
        user_id = application.user_id
        if user_id in seen_users:
            continue
        seen_users.add(user_id)
        display_name = (
            (application.name or '').strip()
            or application.user.get_full_name()
            or application.user.email
            or application.user.username
        )
        contacts.append({
            'user_id': user_id,
            'name': display_name,
            'phone': phone,
            'email': application.user.email,
        })
    return contacts[:50]


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def whatsapp_cloud_webhook(request):
    if request.method == 'GET':
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token', '')
        challenge = request.GET.get('hub.challenge', '')
        if mode == 'subscribe' and token:
            for config in WhatsAppCloudConfig.objects.filter(enabled=True).only('verify_token_encrypted'):
                stored_token = decrypt_secret(config.verify_token_encrypted)
                if stored_token and hmac.compare_digest(stored_token, token):
                    return HttpResponse(challenge, content_type='text/plain')
        return HttpResponse('Webhook verification failed.', status=403)

    raw_body = request.body
    try:
        payload = json.loads(raw_body.decode('utf-8'))
        process_webhook_payload(payload, raw_body, request.headers.get('X-Hub-Signature-256', ''))
        return HttpResponse('EVENT_RECEIVED', content_type='text/plain')
    except (ValueError, WhatsAppCloudError) as exc:
        logger.warning('WhatsApp webhook rejected: %s', exc)
        return HttpResponse('Webhook rejected.', status=403)
    except Exception:
        logger.exception('WhatsApp webhook processing failed')
        return HttpResponse('Webhook processing failed.', status=500)


@login_required
def whatsapp_conversations(request):
    if not _is_pg_admin(request.user):
        messages.error(request, 'PG Admin access required.')
        return redirect('dashboard')
    pg = _active_admin_pg(request)
    if not pg:
        messages.error(request, 'No assigned PG found.')
        return redirect('dashboard')
    config = getattr(pg, 'whatsapp_cloud_config', None)
    if not config or not config.section_enabled('whatsapp_messages'):
        messages.error(request, 'Cloud WhatsApp Messages is not enabled for this PG.')
        return redirect('dashboard')

    if request.method == 'POST':
        phone = request.POST.get('phone', '')
        user_id = request.POST.get('user_id', '')
        if user_id:
            application = ResidentApplication.objects.filter(pg=pg, user_id=user_id).select_related('user').first()
            if not application:
                messages.error(request, 'Selected contact is not part of this PG.')
                return redirect(f"{request.path}?pg={pg.id}")
            phone = application.whatsapp_number or application.phone or ''
        text = request.POST.get('message', '')
        try:
            sent = send_cloud_message(
                pg=pg, to=phone, text=text, section='whatsapp_messages', sent_by=request.user
            )
            messages.success(request, 'WhatsApp message sent.')
            return redirect(f"{request.path}?pg={pg.id}&conversation={sent.conversation_id}")
        except WhatsAppCloudError as exc:
            messages.error(request, str(exc))

    conversations = WhatsAppConversation.objects.filter(pg=pg).select_related('contact').order_by(
        '-last_message_at', '-updated_at'
    )
    selected = None
    selected_id = request.GET.get('conversation')
    if selected_id:
        selected = conversations.filter(pk=selected_id).first()
    if not selected:
        selected = conversations.first()
    thread_messages = []
    if selected:
        thread_messages = list(selected.messages.select_related('sent_by').order_by('-created_at')[:200])[::-1]
        if selected.unread_count:
            selected.unread_count = 0
            selected.save(update_fields=['unread_count', 'updated_at'])
    contact_search = (request.GET.get('contact_q') or '').strip()
    return render(request, 'pgadmin/whatsapp_conversations.html', {
        'pg': pg,
        'pgs': list(PG.objects.filter(admins__user=request.user).distinct().order_by('name')),
        'cloud_config': config,
        'conversations': conversations,
        'selected_conversation': selected,
        'thread_messages': thread_messages,
        'contact_search': contact_search,
        'pg_user_contacts': _pg_user_contacts(pg, contact_search),
    })


@login_required
@require_POST
def whatsapp_cloud_send(request):
    if not _is_pg_admin(request.user):
        return JsonResponse({'ok': False, 'error': 'PG Admin access required.'}, status=403)
    pg = _active_admin_pg(request)
    if not pg:
        return JsonResponse({'ok': False, 'error': 'No assigned PG found.'}, status=403)
    try:
        data = json.loads(request.body or b'{}')
        message = send_cloud_message(
            pg=pg,
            to=data.get('phone', ''),
            text=data.get('message', ''),
            media_url=data.get('media_url', ''),
            media_type=data.get('media_type', 'image'),
            section=data.get('section', ''),
            sent_by=request.user,
        )
        return JsonResponse({'ok': True, 'message_id': message.id, 'status': message.status})
    except (ValueError, WhatsAppCloudError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
