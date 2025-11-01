from decimal import Decimal
import logging

from django.core.mail import EmailMultiAlternatives
import smtplib
from django.db.models import Case, IntegerField, Value, When
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from bookings.models import Booking, ReferralCredit

from .models import Payment

logger = logging.getLogger(__name__)


def _build_receipt_context(payment: Payment) -> dict:
    user = payment.user
    pg = payment.pg

    # Tenant & room details
    tenant_name = ''
    if hasattr(user, 'get_full_name'):
        tenant_name = (user.get_full_name() or '').strip()
    if not tenant_name:
        tenant_name = f"{(getattr(user, 'first_name', '') or '').strip()} {(getattr(user, 'last_name', '') or '').strip()}".strip()
    if not tenant_name:
        tenant_name = getattr(user, 'email', '') or str(user)

    booking = (
        Booking.objects.filter(user=user, room__pg=pg)
        .select_related('room')
        .annotate(
            status_priority=Case(
                When(status=Booking.APPROVED, then=Value(0)),
                When(status=Booking.PENDING, then=Value(1)),
                When(status=Booking.COMPLETED, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        )
        .order_by('status_priority', '-joining_date', '-start_date', '-created_at')
        .first()
    )
    room_no = getattr(getattr(booking, 'room', None), 'room_no', '') or ''

    # Payment metadata
    payment_date_display = payment.date.strftime('%d %b %Y') if payment.date else timezone.localdate().strftime('%d %b %Y')
    amount_display = f"{Decimal(payment.amount):.2f}"
    mode_map = {
        'upi': 'UPI',
        'cash': 'Cash',
        'bank': 'Bank Transfer',
    }
    payment_mode_label = mode_map.get(payment.mode, payment.mode.title() if payment.mode else 'Unknown')

    from_date_display = payment.from_date.strftime('%d %b %Y') if payment.from_date else ''
    to_date_display = payment.to_date.strftime('%d %b %Y') if payment.to_date else ''
    show_period = bool(from_date_display or to_date_display)

    # Determine receipt label based on payment type
    if payment.type == 'advance':
        receipt_label = 'Advance Amount'
    elif payment.type == 'daywise':
        receipt_label = 'Day-wise Receipt'
    else:
        month_source = payment.from_date or payment.date
        month_label = month_source.strftime('%B %Y') if month_source else ''
        receipt_label = f"Monthly Rent ({month_label})" if month_label else 'Monthly Rent'

    pg_address = getattr(pg, 'address', '') or ''
    pg_address_lines = [line for line in pg_address.splitlines() if line.strip()]

    note_lines = [
        "Once paid the rent amount won't be refunded or transferred to anyone.",
        "If you want to vacate please inform to management before 30 days of your joining date otherwise advance amount will not be refunded.",
    ]

    # Populate joining date for advance receipts and referral info for monthly receipts
    joining_date_display = ''
    if payment.type == 'advance' and booking and getattr(booking, 'joining_date', None):
        joining_date_display = booking.joining_date.strftime('%d %b %Y')

    # Determine month key to look up redeemed referral credits.
    # Only use explicit `from_date` on the payment as the canonical billing month.
    # This avoids matching credits when a payment's transaction date falls in a different month.
    month_key = None
    if payment.from_date:
        month_key = payment.from_date.replace(day=1)

    referral_applied = []
    referral_total = 0
    # Only include referral credits on monthly ('fee') receipts for the matching month.
    # CRITICAL: Only show referral credits to the referrer_user (the person who earned the credit)
    # Filter by:
    # 1. PG match
    # 2. redeemed_for_month matches the payment's billing month (from_date)
    # 3. redeemed_on is not null (credit was actually applied)
    # 4. referrer_user matches the payment user (ONLY show to the person who earned the credit)
    if payment.type == 'fee' and month_key and pg and user:
        credits_qs = ReferralCredit.objects.filter(
            pg=pg, 
            redeemed_for_month=month_key, 
            redeemed_on__isnull=False,
            referrer_user=user  # CRITICAL: Only show credits earned by this user
        ).select_related('referrer_user', 'referred_user', 'referrer_booking', 'referred_booking')
        for c in credits_qs:
            referrer_name = ''
            try:
                ref_user = c.referrer_user
                referrer_name = (ref_user.get_full_name() or '').strip() or getattr(ref_user, 'email', '') or str(ref_user)
            except Exception:
                referrer_name = ''
            referral_applied.append({
                'id': c.id,
                'referrer_name': referrer_name,
                'amount': f"{c.amount:.2f}" if c.amount is not None else '0.00',
                'redeemed_amount': f"{(c.redeemed_amount or c.amount or 0):.2f}",
                'referred_user': getattr(getattr(c, 'referred_user', None), 'email', '') or '',
                'notes': c.notes or '',
            })
            referral_total += float(c.redeemed_amount or c.amount or 0)

    return {
        'pg_name': getattr(pg, 'name', '') or 'PG',
        'pg_address_lines': pg_address_lines,
        'pg_address': pg_address,
        'pg_phone': getattr(pg, 'phone', '') or '',
        'receipt_label': receipt_label,
        'tenant_name': tenant_name,
        'room_number': room_no,
        'amount_paid': amount_display,
        'payment_date': payment_date_display,
        'payment_mode_label': payment_mode_label,
        'from_date': from_date_display,
        'to_date': to_date_display,
        'show_period': show_period,
        'joining_date': joining_date_display,
        'referral_applied': referral_applied,
        'referral_total': f"{referral_total:.2f}",
        'payment_type': getattr(payment, 'type', ''),
        'note_lines': note_lines,
        'signature_label': 'Signature',
        'payment_notes': payment.notes or '',
        'payment_reference': payment.id,
        'current_year': timezone.now().year,
    }


def deliver_payment_receipt(payment: Payment) -> None:
    """Render and send the payment receipt email for the given payment."""
    recipient = getattr(payment.user, 'email', None)
    if not recipient:
        raise ValueError('Payment has no recipient email address.')

    context = _build_receipt_context(payment)
    subject = f"Payment Receipt - {context['receipt_label']}"
    html_body = render_to_string('email/payments/receipt.html', context)
    text_body = strip_tags(html_body)
    message = EmailMultiAlternatives(subject, text_body, to=[recipient])
    message.attach_alternative(html_body, 'text/html')
    # Sending emails can fail due to provider limits or transient SMTP issues (e.g. Gmail daily limits).
    # Handle SMTP errors explicitly so a single failed email doesn't raise uncaught exceptions in signals.
    try:
        # use fail_silently=False so django still attempts to raise for non-SMTP issues, but we catch SMTP exceptions below
        message.send(fail_silently=False)
    except smtplib.SMTPDataError as e:
        # Typical case when SMTP provider enforces a sending limit (550 5.4.5 ...)
        logger.warning(
            "SMTP data error when sending payment receipt for payment %s to %s: %s",
            payment.pk,
            recipient,
            getattr(e, 'smtp_error', e)
        )
        # Don't re-raise; the outer signal handler will log the failure as well. Consider enqueueing a retry in production.
    except smtplib.SMTPException as e:
        # Generic SMTP exception (connection, auth, etc.)
        logger.exception("SMTP error when sending payment receipt for payment %s to %s: %s", payment.pk, recipient, e)


@receiver(post_save, sender=Payment)
def send_payment_receipt(sender, instance: Payment, created: bool, **kwargs):
    """Send a payment receipt email whenever a successful payment is created."""
    if not created or instance.status != 'success':
        return

    try:
        deliver_payment_receipt(instance)
    except Exception:
        logger.exception("Failed to send payment receipt for payment %s", instance.pk)