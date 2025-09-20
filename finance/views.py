from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from pgadmin.models import PG
from .models import Fees, Payment, Expenditure
from core.audit import log
from .forms import FeesForm, PaymentForm, ExpenditureForm
from bookings.models import Booking
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone


def _require_pg_admin(user):
    """Unified PG admin check.
    - Allow superusers and website admins into PG-admin/finance areas
    - Otherwise require explicit PG admin capability (profile flag) or membership
    """
    # Superusers and website admins can access PG-admin area
    if getattr(user, 'is_superuser', False):
        return True
    if hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False):
        return True
    # Fall back to explicit PGAdmin membership or profile flag
    try:
        from pgadmin.models import PGAdmin
        if PGAdmin.objects.filter(user=user).exists():
            return True
    except Exception:
        pass
    return hasattr(user, 'profile') and getattr(user.profile, 'is_pg_admin', False) and getattr(user.profile, 'status', 'active') == 'active'


def _admin_pgs(user):
    """PGs visible/manageable by the current user.
    - Superusers and website admins see all PGs
    - Regular PG admins see only their assigned PGs
    """
    if getattr(user, 'is_superuser', False) or (hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False)):
        return PG.objects.all().order_by('name')
    return PG.objects.filter(admins__user=user).order_by('name')


def _active_pg(request):
    qs = _admin_pgs(request.user)
    pg = None
    pg_id = request.GET.get('pg') or request.session.get('active_pg_id')
    if pg_id:
        pg = qs.filter(id=pg_id).first()
    if not pg:
        pg = qs.first()
    if pg:
        request.session['active_pg_id'] = pg.id
    return pg


@login_required
def fees_list(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    items = Fees.objects.filter(pg=pg) if pg else []
    return render(request, 'finance/fees_list.html', {"pg": pg, "items": items, "pgs": list(_admin_pgs(request.user))})


@login_required
def fees_edit(request, pk=None):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    instance = get_object_or_404(Fees, pk=pk, pg=pg) if pk else None
    if request.method == 'POST':
        form = FeesForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.pg = pg
            obj.save()
            log(request.user, 'fees_saved', 'Fees', obj.id)
            messages.success(request, "Fees saved.")
            return redirect('fees_list')
    else:
        form = FeesForm(instance=instance)
    return render(request, 'finance/fees_form.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})


@login_required
def payments_list(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    items = Payment.objects.filter(pg=pg).select_related('user') if pg else []
    return render(request, 'finance/payments_list.html', {"pg": pg, "items": items, "pgs": list(_admin_pgs(request.user))})


@login_required
def payments_edit(request, pk=None):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    instance = get_object_or_404(Payment, pk=pk, pg=pg) if pk else None
    # Build queryset of users for this PG with an approved, active booking (not yet left)
    user_qs = []
    room_map = {}
    if pg:
        # Active approved bookings: start_date set, leaving_date not set
        active_bks = Booking.objects.filter(
            status=Booking.APPROVED,
            room__pg=pg,
            start_date__isnull=False,
            leaving_date__isnull=True,
        ).select_related('user', 'room')
        user_ids = []
        for b in active_bks:
            user_ids.append(b.user_id)
            room_map[b.user_id] = b.room.room_no
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user_qs = User.objects.filter(id__in=user_ids).order_by('first_name', 'last_name')

    if request.method == 'POST':
        form = PaymentForm(request.POST, instance=instance, user_queryset=user_qs, room_map=room_map)
        if form.is_valid():
            prev_status = instance.status if instance else None
            obj = form.save(commit=False)
            obj.pg = pg
            obj.save()
            # Send receipt email only when transitioning to success or creating as success
            try:
                if obj.status == 'success' and (prev_status != 'success'):
                    _send_payment_receipt_email(obj)
            except Exception as e:
                # Do not block UI on email failures; log and continue
                messages.warning(request, f"Payment saved, but receipt email failed: {e}")
            log(request.user, 'payment_saved', 'Payment', obj.id)
            messages.success(request, "Payment saved.")
            return redirect('payments_list')
    else:
        form = PaymentForm(instance=instance, user_queryset=user_qs, room_map=room_map)
    return render(request, 'finance/payments_form.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})


def _send_payment_receipt_email(payment: Payment) -> None:
    """Render and send a payment receipt email to the payer.
    Uses template: email/payments/receipt.html
    """
    # Resolve room number (latest active approved booking for this user in this PG)
    booking = (
        Booking.objects.filter(
            user=payment.user,
            room__pg=payment.pg,
            status=Booking.APPROVED,
            leaving_date__isnull=True,
        )
        .select_related('room')
        .order_by('-created_at')
        .first()
    )
    room_number = getattr(getattr(booking, 'room', None), 'room_no', '—')
    pg = payment.pg

    context = {
        'tenant_name': f"{(payment.user.first_name or '').strip()} {(payment.user.last_name or '').strip()}".strip() or payment.user.email,
        'pg_name': pg.name,
        'room_number': room_number,
        'payment_date': payment.date.strftime('%Y-%m-%d') if payment.date else timezone.now().date().strftime('%Y-%m-%d'),
        'payment_type': dict(Payment.TYPE_CHOICES).get(payment.type, payment.type),
        'payment_method': dict(Payment.MODE_CHOICES).get(payment.mode, payment.mode),
        'amount_paid': f"{payment.amount:.2f}",
        'pg_phone': pg.phone or '',
        'current_year': timezone.now().year,
        'pg_address_short': (pg.address.splitlines()[0] if pg.address else ''),
    }

    # Sanitize WhatsApp phone: digits only, include country code (default to +91 if 10 digits)
    try:
        import re
        raw = context['pg_phone']
        digits = re.sub(r"\D", "", raw or "")
        # handle common India patterns: leading 0, 10-digit local
        if digits.startswith('0') and len(digits) > 1:
            digits = digits.lstrip('0')
        if len(digits) == 10:
            digits = '91' + digits
        context['whatsapp_phone'] = digits
    except Exception:
        context['whatsapp_phone'] = ''

    subject = f"Payment Receipt — {pg.name}"
    from_email = None  # Use DEFAULT_FROM_EMAIL if configured
    to = [payment.user.email]

    html_body = render_to_string('email/payments/receipt.html', context)
    text_body = strip_tags(html_body)

    msg = EmailMultiAlternatives(subject, text_body, from_email, to)
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=True)


@login_required
def expenditure_list(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    items = Expenditure.objects.filter(pg=pg) if pg else []
    return render(request, 'finance/expenditure_list.html', {"pg": pg, "items": items, "pgs": list(_admin_pgs(request.user))})


@login_required
def expenditure_edit(request, pk=None):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    instance = get_object_or_404(Expenditure, pk=pk, pg=pg) if pk else None
    if request.method == 'POST':
        form = ExpenditureForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.pg = pg
            obj.save()
            log(request.user, 'expenditure_saved', 'Expenditure', obj.id)
            messages.success(request, "Expenditure saved.")
            return redirect('expenditure_list')
    else:
        form = ExpenditureForm(instance=instance)
    return render(request, 'finance/expenditure_form.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})
from django.shortcuts import render

# Create your views here.
