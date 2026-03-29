import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.timezone import localtime

from .models import Complaint

logger = logging.getLogger(__name__)


def _sender_name(user):
    if not user:
        return 'System'
    if hasattr(user, 'get_full_name'):
        full = (user.get_full_name() or '').strip()
        if full:
            return full
    return getattr(user, 'email', '') or str(user)


def _thread_items(complaint: Complaint, include_internal: bool = False):
    items = [
        {
            'sender': _sender_name(complaint.user),
            'sender_role': 'Tenant',
            'message': complaint.description,
            'timestamp': localtime(complaint.created_at),
            'is_internal': False,
        }
    ]

    comments_qs = complaint.comments.select_related('user').order_by('created_at')
    if not include_internal:
        comments_qs = comments_qs.filter(is_internal=False)

    for c in comments_qs:
        role = 'PG Admin'
        if c.user_id == complaint.user_id:
            role = 'Tenant'
        items.append(
            {
                'sender': _sender_name(c.user),
                'sender_role': role,
                'message': c.comment,
                'timestamp': localtime(c.created_at),
                'is_internal': c.is_internal,
            }
        )
    return items


def _send_complaint_email(recipient_email: str, subject: str, context: dict):
    if not recipient_email:
        return

    html_body = render_to_string('email/complaints/update.html', context)
    text_body = strip_tags(html_body)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=[recipient_email],
    )
    msg.attach_alternative(html_body, 'text/html')
    msg.send(fail_silently=True)


def notify_new_complaint(complaint: Complaint):
    """New complaint: notify tenant + all admins of complaint PG."""
    thread = _thread_items(complaint, include_internal=False)
    base_context = {
        'event_label': 'New Complaint Raised',
        'complaint': complaint,
        'thread_items': thread,
        'latest_sender': _sender_name(complaint.user),
    }

    # Tenant copy
    try:
        _send_complaint_email(
            recipient_email=getattr(complaint.user, 'email', ''),
            subject=f"PG-MS: Complaint #{complaint.id} Received",
            context=base_context,
        )
    except Exception:
        logger.exception('Failed sending complaint creation email to tenant for complaint %s', complaint.id)

    # Admin copies
    admin_users = [a.user for a in complaint.pg.admins.select_related('user').all()]
    for admin_user in admin_users:
        try:
            _send_complaint_email(
                recipient_email=getattr(admin_user, 'email', ''),
                subject=f"PG-MS: New Complaint #{complaint.id} - {complaint.title}",
                context=base_context,
            )
        except Exception:
            logger.exception('Failed sending complaint creation email to admin for complaint %s', complaint.id)


def notify_admin_comment(comment):
    """Admin added a public comment: notify tenant."""
    complaint = comment.complaint
    if comment.is_internal:
        return

    try:
        _send_complaint_email(
            recipient_email=getattr(complaint.user, 'email', ''),
            subject=f"PG-MS: Update on Complaint #{complaint.id}",
            context={
                'event_label': 'PG Admin Updated Your Complaint',
                'complaint': complaint,
                'thread_items': _thread_items(complaint, include_internal=False),
                'latest_sender': _sender_name(comment.user),
            },
        )
    except Exception:
        logger.exception('Failed sending admin comment email for complaint %s', complaint.id)


def notify_user_comment(comment):
    """Tenant added a comment: notify PG admins."""
    complaint = comment.complaint
    admin_users = [a.user for a in complaint.pg.admins.select_related('user').all()]

    for admin_user in admin_users:
        try:
            _send_complaint_email(
                recipient_email=getattr(admin_user, 'email', ''),
                subject=f"PG-MS: Tenant Replied on Complaint #{complaint.id}",
                context={
                    'event_label': 'Tenant Added a New Comment',
                    'complaint': complaint,
                    'thread_items': _thread_items(complaint, include_internal=False),
                    'latest_sender': _sender_name(comment.user),
                },
            )
        except Exception:
            logger.exception('Failed sending user comment email for complaint %s', complaint.id)
