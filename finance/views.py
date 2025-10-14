from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from pgadmin.models import PG
from .models import Fees, Payment, Expenditure
from core.audit import log
from .forms import FeesForm, PaymentForm, ExpenditureForm
from bookings.models import Booking
from django.db.models import Q, Sum
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.utils.dateparse import parse_date
from datetime import date, timedelta
import calendar
from django.http import HttpResponse, JsonResponse
from decimal import Decimal, InvalidOperation
from io import StringIO
import csv
from .models import ResidentRate, ReminderLog, Adjustment
import importlib
from django.urls import reverse
from urllib.parse import urlencode


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


def _is_authorized_pg(user, pg_id) -> bool:
    """Check if the given user is authorized to access the provided PG id."""
    try:
        pg_id = int(pg_id)
    except Exception:
        return False
    return _admin_pgs(user).filter(id=pg_id).exists()


def _user_related_to_pg(user_obj, pg) -> bool:
    """Return True if the user has any booking/payment/adjustment in the given PG."""
    if not (user_obj and pg):
        return False
    return (
        Booking.objects.filter(user=user_obj, room__pg=pg).exists()
        or Payment.objects.filter(user=user_obj, pg=pg).exists()
        or Adjustment.objects.filter(user=user_obj, pg=pg).exists()
    )


@login_required
def fees_list(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    # Enforce pg param authorization explicitly
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('fees_list')
    pg = _active_pg(request)
    items = Fees.objects.filter(pg=pg) if pg else []
    return render(request, 'finance/fees_list.html', {"pg": pg, "items": items, "pgs": list(_admin_pgs(request.user))})


@login_required
def fees_edit(request, pk=None):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('fees_list')
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
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('payments_list')
    pg = _active_pg(request)
    items = Payment.objects.none()
    q = (request.GET.get('q') or '').strip()
    ym = (request.GET.get('ym') or '').strip()
    month = request.GET.get('month')
    year = request.GET.get('year')
    date_from = parse_date((request.GET.get('date_from') or '').strip())
    date_to = parse_date((request.GET.get('date_to') or '').strip())
    if pg:
        items = Payment.objects.filter(pg=pg).select_related('user')
        # Date filters: explicit date range wins; else month (ym/year+month)
        if date_from or date_to:
            if date_from:
                items = items.filter(date__gte=date_from)
            if date_to:
                items = items.filter(date__lte=date_to)
        else:
            y_val = None
            m_val = None
            if ym and '-' in ym:
                try:
                    y_str, m_str = ym.split('-', 1)
                    y_val = int(y_str)
                    m_val = int(m_str)
                except Exception:
                    y_val = None
                    m_val = None
            elif year and month:
                try:
                    y_val = int(year)
                    m_val = int(month)
                except Exception:
                    y_val = None
                    m_val = None
            if y_val and m_val:
                m_first, m_last, _ = _month_range(y_val, m_val)
                items = items.filter(date__gte=m_first, date__lte=m_last)
        # Search across user name/email, mode, type, notes, and amount
        if q:
            try:
                # Attempt numeric match on amount as well
                from decimal import Decimal
                amt = Decimal(q)
            except Exception:
                amt = None
            name_q = Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(user__email__icontains=q)
            meta_q = Q(mode__icontains=q) | Q(type__icontains=q) | Q(notes__icontains=q)
            items = items.filter(name_q | meta_q | (Q(amount=amt) if amt is not None else Q()))
        # Default ordering: latest first
        items = items.order_by('-date', '-id')

    # Prepare filters context
    filters = {
        'q': q,
        'ym': ym,
        'date_from': request.GET.get('date_from') or '',
        'date_to': request.GET.get('date_to') or '',
        'year': year or '',
        'month': month or '',
    }
    return render(request, 'finance/payments_list.html', {"pg": pg, "items": items, "pgs": list(_admin_pgs(request.user)), "filters": filters})


@login_required
def payments_edit(request, pk=None):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('payments_list')
    pg = _active_pg(request)
    instance = get_object_or_404(Payment, pk=pk, pg=pg) if pk else None
    # Build queryset of users for this PG with an approved, active booking (not yet left)
    user_qs = []
    room_map = {}
    if pg:
        # Base: active approved bookings (preferred list for new payments)
        active_bks = Booking.objects.filter(
            status=Booking.APPROVED,
            room__pg=pg,
            start_date__isnull=False,
            leaving_date__isnull=True,
        ).select_related('user', 'room')
        user_ids = set()
        for b in active_bks:
            user_ids.add(b.user_id)
            room_map[b.user_id] = b.room.room_no
        # When editing, ensure the existing payment's user is selectable even if not currently active
        if instance:
            user_ids.add(instance.user_id)
        # Also include any users who have prior payments in this PG (helps edits for past residents)
        prior_payer_ids = Payment.objects.filter(pg=pg).values_list('user_id', flat=True).distinct()
        user_ids.update(prior_payer_ids)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if user_ids:
            user_qs = User.objects.filter(id__in=list(user_ids)).order_by('first_name', 'last_name')
        else:
            user_qs = User.objects.none()

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


@login_required
def payments_delete(request, pk: int):
    """Delete a payment entry. POST only.
    - Requires PG admin privileges and access to the payment's PG.
    - After deletion, redirect back to payments list for that PG.
    """
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    # Only allow POST to delete
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('payments_list')
    # Determine active PG for context and authorization
    pg = _active_pg(request)
    # Fetch payment and ensure it belongs to an authorized PG
    payment = get_object_or_404(Payment, pk=pk)
    if not _is_authorized_pg(request.user, payment.pg_id):
        messages.error(request, "You do not have access to this PG.")
        return redirect('payments_list')
    # Delete and log
    pid = payment.id
    payment.delete()
    log(request.user, 'payment_deleted', 'Payment', pid)
    messages.success(request, "Payment deleted.")
    return redirect('payments_list')


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
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('expenditure_list')
    pg = _active_pg(request)
    items = Expenditure.objects.none()
    total = 0
    # Filters
    q = (request.GET.get('q') or '').strip()
    category = (request.GET.get('category') or '').strip()
    date_from = parse_date((request.GET.get('date_from') or '').strip())
    date_to = parse_date((request.GET.get('date_to') or '').strip())
    if pg:
        items = Expenditure.objects.filter(pg=pg)
        if category:
            items = items.filter(category=category)
        if date_from:
            items = items.filter(date__gte=date_from)
        if date_to:
            items = items.filter(date__lte=date_to)
        if q:
            items = items.filter(Q(notes__icontains=q) | Q(category__icontains=q))
        items = items.order_by('-date', '-id')
        total = items.aggregate(total=Sum('amount')).get('total') or 0
    filters = {
        'q': q,
        'category': category,
        'date_from': request.GET.get('date_from') or '',
        'date_to': request.GET.get('date_to') or '',
    }
    return render(request, 'finance/expenditure_list.html', {"pg": pg, "items": items, "pgs": list(_admin_pgs(request.user)), "filters": filters, "total": total})


@login_required
def expenditure_edit(request, pk=None):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('expenditure_list')
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


def _month_range(year: int, month: int):
    first = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    return first, last, last_day


def _overlap_days(start: date, end: date | None, m_first: date, m_last: date) -> int:
    # Treat None end as open-ended
    stay_start = max(start, m_first)
    stay_end = min(end or m_last, m_last)
    if stay_end < stay_start:
        return 0
    return (stay_end - stay_start).days + 1


def _payment_anchor_for_booking(booking) -> date | None:
    payment_anchor = getattr(booking, 'payment_date', None) or booking.joining_date or booking.start_date
    if not payment_anchor and getattr(booking, 'created_at', None):
        created = booking.created_at
        if created:
            if timezone.is_aware(created):
                created = timezone.localtime(created)
            payment_anchor = created.date()
    return payment_anchor


def _payment_due_for_month(booking, m_first: date, m_days: int) -> date | None:
    anchor = _payment_anchor_for_booking(booking)
    if not anchor:
        return None
    due_day = min(anchor.day, m_days)
    return date(m_first.year, m_first.month, due_day)


def _resolve_status(expected: float, collected: float, m_first: date, due_date: date | None, today: date | None = None):
    if today is None:
        today = timezone.now().date()
    month_marker = (m_first.year, m_first.month)
    today_marker = (today.year, today.month)

    if month_marker > today_marker:
        due_passed = False
    elif month_marker < today_marker:
        due_passed = True
    else:
        if due_date:
            due_passed = today >= due_date
        else:
            due_passed = True

    if due_passed:
        if collected >= expected - 0.5:
            return 'paid', 'Paid', 'status-paid'
        if collected > 0:
            return 'partial', 'Partial', 'status-partial'
        return 'unpaid', 'Unpaid', 'status-unpaid'

    return 'upcoming', 'Not due', 'status-upcoming'


def _expected_rent_for_user_pg_month(u, pg, booking, m_first, m_last, today=None) -> float:
    # Custom rate override
    rr = ResidentRate.objects.filter(user=u, pg=pg, active=True).first()
    if rr:
        monthly = float(rr.amount)
    else:
        # Derive from Fees by share type (room.total_shares)
        share_type = str(getattr(getattr(booking, 'room', None), 'total_shares', '') or '')
        fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
        monthly = float(fees.monthly_fee) if fees else 0.0
    # Pro-rate by overlap days
    days_in_month = (m_last - m_first).days + 1
    # Use joining_date as the primary start for pro-rating; fallback to start_date then created_at
    start = booking.joining_date or booking.start_date or booking.created_at.date()
    end = booking.leaving_date
    stayed = _overlap_days(start, end, m_first, m_last)
    if stayed <= 0 or monthly <= 0:
        return 0.0
    expected = round((monthly * stayed) / days_in_month, 2)

    if today is None:
        today = timezone.now().date()

    payment_anchor = _payment_anchor_for_booking(booking)

    month_marker = (m_first.year, m_first.month)
    today_marker = (today.year, today.month)

    # Future months are not yet due regardless of anchor
    if month_marker > today_marker:
        return 0.0

    if payment_anchor:
        due_day = payment_anchor.day
        due_day = min(due_day, days_in_month)
        due_date = date(m_first.year, m_first.month, due_day)
        if month_marker == today_marker and today < due_date:
            return 0.0

    return expected


@login_required
def expenditure_export_pdf(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    pg = _active_pg(request)
    # Apply same filters as list view
    q = (request.GET.get('q') or '').strip()
    category = (request.GET.get('category') or '').strip()
    date_from = parse_date((request.GET.get('date_from') or '').strip())
    date_to = parse_date((request.GET.get('date_to') or '').strip())
    items = Expenditure.objects.none()
    if pg:
        items = Expenditure.objects.filter(pg=pg)
        if category:
            items = items.filter(category=category)
        if date_from:
            items = items.filter(date__gte=date_from)
        if date_to:
            items = items.filter(date__lte=date_to)
        items = items.order_by('date', 'id')  # chronological for export

    # Lazy import ReportLab
    rl_pagesizes = importlib.util.find_spec('reportlab.lib.pagesizes')
    rl_units = importlib.util.find_spec('reportlab.lib.units')
    rl_canvas = importlib.util.find_spec('reportlab.pdfgen.canvas')
    rl_colors = importlib.util.find_spec('reportlab.lib.colors')
    if not (rl_pagesizes and rl_units and rl_canvas and rl_colors):
        return HttpResponse("ReportLab not installed. Please install reportlab to enable PDF export.", status=400)
    A4 = importlib.import_module('reportlab.lib.pagesizes').A4
    mm = importlib.import_module('reportlab.lib.units').mm
    canvas = importlib.import_module('reportlab.pdfgen.canvas').Canvas
    colors = importlib.import_module('reportlab.lib.colors')

    resp = HttpResponse(content_type='application/pdf')
    pg_name = (pg.name if pg else 'PG')
    def _safe_name(s: str) -> str:
        return ''.join(c for c in s if c.isalnum() or c in (' ', '-', '_')).strip()
    filename = f"{_safe_name(pg_name)}-expenditures.pdf"
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'

    p = canvas(resp, pagesize=A4)
    width, height = A4
    x_margin = 15 * mm
    y = height - 20 * mm

    # Header
    p.setTitle("Expenditures Report")
    p.setFont("Helvetica-Bold", 14)
    p.drawString(x_margin, y, f"Expenditures Report")
    y -= 6 * mm
    p.setFont("Helvetica", 10)
    if pg:
        p.drawString(x_margin, y, f"PG: {pg.name}")
        y -= 5 * mm
    # Filters summary line
    fil_parts = []
    if category: fil_parts.append(f"Category: {category}")
    if date_from: fil_parts.append(f"From: {date_from:%Y-%m-%d}")
    if date_to: fil_parts.append(f"To: {date_to:%Y-%m-%d}")
    # search is intentionally ignored for export per requirements
    p.drawString(x_margin, y, f"Filters: {'; '.join(fil_parts) if fil_parts else 'None'}")
    y -= 6 * mm
    p.setFont("Helvetica-Oblique", 9)
    p.drawString(x_margin, y, f"Generated on: {timezone.now().astimezone().strftime('%Y-%m-%d %H:%M')}")
    y -= 8 * mm

    # Table header
    p.setFont("Helvetica-Bold", 10)
    p.drawString(x_margin, y, "Date")
    p.drawString(x_margin + 28 * mm, y, "Category")
    p.drawRightString(x_margin + 75 * mm, y, "Amount (Rs.)")
    p.drawString(x_margin + 85 * mm, y, "Notes")
    y -= 5 * mm
    p.setStrokeColor(colors.grey)
    p.line(x_margin, y, width - x_margin, y)
    y -= 5 * mm

    p.setFont("Helvetica", 10)
    total = 0
    for i in items:
        if y < 25 * mm:
            p.showPage()
            p.setFont("Helvetica-Bold", 10)
            y = height - 20 * mm
            p.drawString(x_margin, y, "Date")
            p.drawString(x_margin + 28 * mm, y, "Category")
            p.drawRightString(x_margin + 75 * mm, y, "Amount (Rs.)")
            p.drawString(x_margin + 85 * mm, y, "Notes")
            y -= 5 * mm
            p.setStrokeColor(colors.grey)
            p.line(x_margin, y, width - x_margin, y)
            y -= 5 * mm
            p.setFont("Helvetica", 10)
        # Row values
        p.drawString(x_margin, y, i.date.strftime('%Y-%m-%d'))
        p.drawString(x_margin + 28 * mm, y, str(i.get_category_display() if hasattr(i, 'get_category_display') else i.category))
        p.drawRightString(x_margin + 75 * mm, y, f"{float(i.amount):.2f}")
        # Truncate notes to fit line width
        note = (i.notes or '').replace('\n', ' ')
        max_chars = 80
        if len(note) > max_chars:
            note = note[:max_chars-1] + '…'
        p.drawString(x_margin + 85 * mm, y, note)
        y -= 6 * mm
        total += float(i.amount)

    # Totals footer
    if y < 20 * mm:
        p.showPage()
        y = height - 25 * mm
    p.setFont("Helvetica-Bold", 11)
    p.drawRightString(x_margin + 75 * mm, y, f"Total: Rs. {total:.2f}")

    p.showPage()
    p.save()
    return resp


def _collected_for_user_pg_month(u, pg, m_first, m_last) -> float:
    # Count only rent/fee payments for the selected month; exclude advances and adjustments
    p_sum = Payment.objects.filter(
        user=u, pg=pg, status='success', type='fee', date__gte=m_first, date__lte=m_last
    ).aggregate(total=Sum('amount')).get('total') or 0
    return float(p_sum)


def _advance_paid_for_user_pg(u, pg) -> float:
    adv = Payment.objects.filter(user=u, pg=pg, status='success', type='advance').aggregate(total=Sum('amount')).get('total') or 0
    return float(adv)


@login_required
def monthly_dashboard(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    # If a pg is explicitly requested, ensure user is authorized for it
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('finance_monthly')
    pg = _active_pg(request)
    if not pg:
        return render(request, 'finance/monthly_dashboard.html', {"pg": None, "rows": [], "summary": {}})
    # Parse month filter
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, m_days = _month_range(year, month)
    # Prev/next month helpers
    def _shift(y, m, delta):
        m2 = m + delta
        y2 = y + (m2 - 1) // 12
        m2 = ((m2 - 1) % 12) + 1
        return y2, m2
    prev_year, prev_month = _shift(year, month, -1)
    next_year, next_month = _shift(year, month, +1)

    # Sorting parameters (default: room number ascending)
    sort_key = (request.GET.get('sort') or 'room').strip().lower()
    sort_dir = (request.GET.get('dir') or 'asc').strip().lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'asc'

    # Active residents overlapping with month. Select ONE booking per user: the one with max overlap days in the month
    active_bks = (
        Booking.objects.filter(
            status__in=[Booking.APPROVED, Booking.COMPLETED],
            room__pg=pg,
        )
        .select_related('user', 'room', 'user__profile')
    )

    # Group all overlapping bookings per user; sum expected across re-joins in the same month
    by_user = {}
    for b in active_bks:
        start = b.joining_date or b.start_date or b.created_at.date()
        end = b.leaving_date
        ov = _overlap_days(start, end, m_first, m_last)
        if ov <= 0:
            continue
        by_user.setdefault(b.user_id, []).append(b)

    rows = []
    total_expected = 0.0
    total_collected = 0.0
    for user_id, bookings in by_user.items():
        # Sort segments by start date for deterministic output
        segs = []
        primary_seg = None
        for b in bookings:
            s = b.joining_date or b.start_date or b.created_at.date()
            e = b.leaving_date
            rr = ResidentRate.objects.filter(user=b.user, pg=pg, active=True).first()
            if rr:
                base_monthly = float(rr.amount)
                base_source = 'Custom rate'
            else:
                share_type = str(getattr(getattr(b, 'room', None), 'total_shares', '') or '')
                fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
                base_monthly = float(getattr(fees, 'monthly_fee', 0) or 0)
                base_source = f"{share_type}-Sharing fee" if share_type else 'Default fee'
            exp_part = _expected_rent_for_user_pg_month(b.user, pg, b, m_first, m_last)
            stayed = _overlap_days(s, e, m_first, m_last)
            payment_anchor = _payment_anchor_for_booking(b)
            due_date = _payment_due_for_month(b, m_first, m_days)
            segs.append({
                'b': b, 'start': s, 'end': e, 'base': base_monthly, 'source': base_source,
                'expected': exp_part, 'stayed': stayed,
                'room_no': getattr(b.room, 'room_no', '—'),
                'payment_anchor': payment_anchor,
                'payment_due': due_date,
                'booking_id': b.id,
            })
            if (primary_seg is None) or (stayed > primary_seg.get('stayed', 0)):
                primary_seg = segs[-1]
        segs.sort(key=lambda x: (x['start'] or m_first, x['end'] or m_last))

        expected_total = round(sum((seg['expected'] or 0.0) for seg in segs), 2)
        # Collected for the user across the month (unchanged)
        u = segs[0]['b'].user
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        pending = round(expected_total - collected, 2)
        primary_due = primary_seg.get('payment_due') if primary_seg else None
        status, status_label, status_css = _resolve_status(expected_total, float(collected), m_first, primary_due, today)
        # Phone sanitize
        try:
            import re
            raw = getattr(getattr(u, 'profile', None), 'phone', '') or ''
            digits = re.sub(r"\D", "", raw)
            if digits.startswith('0') and len(digits) > 1:
                digits = digits.lstrip('0')
            if len(digits) == 10:
                digits = '91' + digits
        except Exception:
            digits = ''
        # Tooltip (single or multi-segment)
        if len(segs) == 1:
            seg = segs[0]
            exp_tip = (
                f"Base: ₹{seg['base']:.2f}/month — {seg['source']}. "
                f"Days: {seg['stayed']}/{m_days} in {calendar.month_abbr[month]} {year}. "
                f"Expected = ₹{seg['expected']:.2f}"
            )
            if seg.get('payment_due'):
                exp_tip += f" Payment date: {seg['payment_due'].strftime('%Y-%m-%d')}"
        else:
            parts = []
            for seg in segs:
                rng = f"{(seg['start'] or m_first).strftime('%d')}–{(seg['end'] or m_last).strftime('%d')}"
                parts.append(
                    f"[{rng}] ₹{seg['base']:.2f} ({seg['source']}), days {seg['stayed']}/{m_days} → ₹{seg['expected']:.2f}"
                )
            exp_tip = (
                f"Multiple stays: " + "; ".join(parts) + f". Total expected = ₹{expected_total:.2f}"
            )
            if primary_seg and primary_seg.get('payment_due'):
                exp_tip += f" Primary payment date: {primary_seg['payment_due'].strftime('%Y-%m-%d')}"
        # Pick joining as earliest start in month; leaving as latest end if present
        earliest_start = min((seg['start'] or m_first) for seg in segs)
        latest_end = None
        ends = [seg['end'] for seg in segs if seg['end']]
        if ends:
            latest_end = max(ends)
        # Choose a representative room_no (latest segment's room)
        last_seg = sorted(segs, key=lambda x: (x['start'] or m_first, x['end'] or m_last))[-1]
        payment_due = primary_seg.get('payment_due') if primary_seg else None
        payment_anchor = primary_seg.get('payment_anchor') if primary_seg else None
        payment_due_day = payment_anchor.day if payment_anchor else None
        primary_booking_id = primary_seg.get('booking_id') if primary_seg else None
        rows.append({
            'user': u,
            'room_no': getattr(last_seg['b'].room, 'room_no', '—'),
            'expected': expected_total,
            'expected_tip': exp_tip,
            'collected': round(float(collected), 2),
            'pending': max(0.0, pending),
            'status': status,
            'status_label': status_label,
            'status_css': status_css,
            'joining': earliest_start,
            'leaving': latest_end,
            'whatsapp_phone': digits,
            'advance': round(_advance_paid_for_user_pg(u, pg), 2),
            'payment_due_date': payment_due,
            'payment_anchor': payment_anchor,
            'payment_due_day': payment_due_day,
            'payment_anchor_iso': payment_anchor.isoformat() if payment_anchor else '',
            'payment_date_iso': payment_due.isoformat() if payment_due else '',
            'primary_booking_id': primary_booking_id,
            'segments': [
                {
                    'room_no': seg['room_no'],
                    'joining': seg['start'],
                    'leaving': seg['end'],
                    'base': seg['base'],
                    'source': seg['source'],
                    'stayed': seg['stayed'],
                    'expected': seg['expected'],
                    'payment_due': seg.get('payment_due'),
                }
                for seg in segs
            ],
            'month_days': m_days,
        })
        total_expected += expected_total
        total_collected += float(collected)

    # Filters
    # Compute total advance across all rows before applying status filter
    total_advance_all = round(sum((r.get('advance') or 0.0) for r in rows), 2)
    only = request.GET.get('only')
    if only in ('paid', 'unpaid', 'partial', 'upcoming'):
        rows = [r for r in rows if r['status'] == only]

    # Apply sorting
    import re
    def _room_sort_val(room_no):
        s = str(room_no or '')
        m = re.search(r"\d+", s)
        if m:
            try:
                return (0, int(m.group()))
            except Exception:
                pass
        # Fallback: place non-numeric after numeric, then by string
        return (1, s.strip().lower())

    def _resident_name(u):
        name = f"{(getattr(u, 'first_name', '') or '').strip()} {(getattr(u, 'last_name', '') or '').strip()}".strip()
        return (name or getattr(u, 'email', '') or '').strip().lower()

    status_order = {'upcoming': -1, 'unpaid': 0, 'partial': 1, 'paid': 2}

    def _key(row):
        if sort_key == 'room':
            return _room_sort_val(row.get('room_no'))
        if sort_key in ('resident', 'user'):
            return _resident_name(row.get('user'))
        if sort_key == 'expected':
            return float(row.get('expected') or 0.0)
        if sort_key == 'collected':
            return float(row.get('collected') or 0.0)
        if sort_key == 'pending':
            return float(row.get('pending') or 0.0)
        if sort_key == 'status':
            return status_order.get(row.get('status'), 99)
        if sort_key == 'joining':
            # None should sort last in asc, first in desc; we encode as (is_none, value)
            j = row.get('joining')
            return (j is None, j or m_first)
        if sort_key == 'leaving':
            l = row.get('leaving')
            return (l is None, l or m_first)
        # Default fallback
        return _room_sort_val(row.get('room_no'))

    rows.sort(key=_key, reverse=(sort_dir == 'desc'))

    # Footer totals for displayed rows
    footer_totals = {
        'advance': round(sum((r.get('advance') or 0.0) for r in rows), 2),
        'expected': round(sum((r.get('expected') or 0.0) for r in rows), 2),
        'collected': round(sum((r.get('collected') or 0.0) for r in rows), 2),
        'pending': round(sum((r.get('pending') or 0.0) for r in rows), 2),
    }

    summary = {
        'year': year, 'month': month,
        'total_expected': round(total_expected, 2),
        'total_collected': round(total_collected, 2),
        'total_pending': round(max(0.0, total_expected - total_collected), 2),
        'total_advance': total_advance_all,
        'counts': {
            'paid': sum(1 for r in rows if r['status'] == 'paid'),
            'partial': sum(1 for r in rows if r['status'] == 'partial'),
            'unpaid': sum(1 for r in rows if r['status'] == 'unpaid'),
            'upcoming': sum(1 for r in rows if r['status'] == 'upcoming'),
        },
        'nav': {
            'prev_year': prev_year, 'prev_month': prev_month,
            'next_year': next_year, 'next_month': next_month,
        }
    }

    return render(request, 'finance/monthly_dashboard.html', {
        'pg': pg,
        'rows': rows,
        'footer_totals': footer_totals,
        'summary': summary,
        'pgs': list(_admin_pgs(request.user)),
        'm_first': m_first,
        'current_sort': sort_key,
        'current_dir': sort_dir,
    })


@login_required
def monthly_update_payment_date(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('finance_monthly')

    booking_id = request.POST.get('booking_id')
    payment_raw = (request.POST.get('payment_date') or '').strip()
    payment_date = parse_date(payment_raw) if payment_raw else None
    if not booking_id or not payment_date:
        messages.error(request, "Select a valid payment date.")
        return redirect('finance_monthly')

    booking = get_object_or_404(Booking, pk=booking_id)
    if not _is_authorized_pg(request.user, booking.pg_id):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('finance_monthly')

    booking.payment_date = payment_date
    booking.save(update_fields=['payment_date'])
    log(request.user, 'booking_payment_date_updated', 'Booking', booking.id)
    messages.success(request, "Payment date updated.")

    params = {}
    for key in ('year', 'month', 'sort', 'dir', 'only', 'pg'):
        val = request.POST.get(key)
        if val not in (None, ''):
            params[key] = val
    redirect_url = reverse('finance_monthly')
    if params:
        redirect_url = f"{redirect_url}?{urlencode(params)}"
    return redirect(redirect_url)


@login_required
def monthly_export_csv(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    pg = _active_pg(request)
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, m_days = _month_range(year, month)

    # Sorting params (default to dashboard defaults)
    sort_key = (request.GET.get('sort') or 'room').strip().lower()
    sort_dir = (request.GET.get('dir') or 'asc').strip().lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'asc'

    # Build rows like dashboard by summing all overlapping stays per user
    active_bks = (
        Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg)
        .select_related('user', 'room', 'user__profile')
    )
    by_user = {}
    for b in active_bks:
        start = b.joining_date or b.start_date or b.created_at.date()
        end = b.leaving_date
        ov = _overlap_days(start, end, m_first, m_last)
        if ov <= 0:
            continue
        by_user.setdefault(b.user_id, []).append(b)
    # Build data rows for sorting
    data_rows = []
    only_filter = (request.GET.get('only') or '').strip().lower()
    for user_id, bookings in by_user.items():
        bookings.sort(key=lambda b: (b.joining_date or b.start_date or b.created_at.date()))
        u = bookings[0].user
        expected = 0.0
        earliest_start = None
        latest_end = None
        due_date = None
        best_overlap = -1
        for b in bookings:
            expected += _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
            s = b.joining_date or b.start_date or b.created_at.date()
            e = b.leaving_date
            earliest_start = min(earliest_start, s) if earliest_start else s
            if e:
                latest_end = max(latest_end, e) if latest_end else e
            overlap = _overlap_days(s, e, m_first, m_last)
            if overlap > 0:
                due_candidate = _payment_due_for_month(b, m_first, m_days)
                if due_candidate and overlap > best_overlap:
                    due_date = due_candidate
                    best_overlap = overlap
        expected = round(expected, 2)
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        pending = round(expected - collected, 2)
        status, status_label, status_css = _resolve_status(expected, float(collected), m_first, due_date, today)
        advance = _advance_paid_for_user_pg(u, pg)
        if only_filter in ('paid', 'partial', 'unpaid', 'upcoming') and status != only_filter:
            continue
        phone_raw = getattr(getattr(u, 'profile', None), 'phone', '') or ''
        data_rows.append({
            'user': u,
            'user_name': (f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email),
            'email': u.email,
            'phone': phone_raw,
            'room_no': getattr(bookings[-1].room, 'room_no', ''),
            'joining': earliest_start,
            'advance': float(advance),
            'expected': float(expected),
            'collected': float(collected),
            'pending': float(max(0.0, pending)),
            'status': status,
            'status_label': status_label,
            'status_css': status_css,
        })

    # Sort rows like dashboard
    import re
    status_order = {'upcoming': -1, 'unpaid': 0, 'partial': 1, 'paid': 2}
    def _room_sort_val(room_no):
        s = str(room_no or '')
        m = re.search(r"\d+", s)
        if m:
            try:
                return (0, int(m.group()))
            except Exception:
                pass
        return (1, s.strip().lower())
    def _resident_name_lower(row):
        return (row.get('user_name') or '').strip().lower()
    def _key(row):
        if sort_key == 'room':
            return _room_sort_val(row.get('room_no'))
        if sort_key in ('resident', 'user'):
            return _resident_name_lower(row)
        if sort_key == 'expected':
            return float(row.get('expected') or 0.0)
        if sort_key == 'collected':
            return float(row.get('collected') or 0.0)
        if sort_key == 'pending':
            return float(row.get('pending') or 0.0)
        if sort_key == 'status':
            return status_order.get(row.get('status'), 99)
        if sort_key == 'joining':
            j = row.get('joining')
            return (j is None, j or m_first)
        # Default
        return _room_sort_val(row.get('room_no'))
    data_rows.sort(key=_key, reverse=(sort_dir == 'desc'))

    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(['User', 'Email', 'Phone', 'Room', 'Joining', 'Advance', 'Expected', 'Collected', 'Pending', 'Status'])
    for r in data_rows:
        w.writerow([
            r['user_name'], r['email'], r['phone'], r['room_no'],
            (r['joining'].strftime('%Y-%m-%d') if r['joining'] else ''),
            f"{r['advance']:.2f}", f"{r['expected']:.2f}", f"{r['collected']:.2f}", f"{r['pending']:.2f}",
            r['status_label'] if r.get('status_label') else r['status']
        ])
    resp = HttpResponse(sio.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="monthly-{year}-{month:02d}.csv"'
    return resp


@login_required
def monthly_export_segments_csv(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    pg = _active_pg(request)
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, m_days = _month_range(year, month)
    # Collect overlapping bookings
    active_bks = (
        Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg)
        .select_related('user', 'room', 'user__profile')
    )
    rows = []
    for b in active_bks:
        s = b.joining_date or b.start_date or b.created_at.date()
        e = b.leaving_date
        ov = _overlap_days(s, e, m_first, m_last)
        if ov <= 0:
            continue
        # base monthly
        rr = ResidentRate.objects.filter(user=b.user, pg=pg, active=True).first()
        if rr:
            base = float(rr.amount)
            source = 'Custom rate'
        else:
            share_type = str(getattr(getattr(b, 'room', None), 'total_shares', '') or '')
            fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
            base = float(getattr(fees, 'monthly_fee', 0) or 0)
            source = f"{share_type}-Sharing fee" if share_type else 'Default fee'
        expected = _expected_rent_for_user_pg_month(b.user, pg, b, m_first, m_last)
        phone_raw = getattr(getattr(b.user, 'profile', None), 'phone', '') or ''
        rows.append([
            b.user.id,
            f"{b.user.first_name} {b.user.last_name}".strip() or b.user.email,
            b.user.email,
            phone_raw,
            getattr(b.room, 'room_no', ''),
            s.strftime('%Y-%m-%d') if s else '',
            e.strftime('%Y-%m-%d') if e else '',
            f"{base:.2f}", source, f"{ov}/{m_days}", f"{expected:.2f}",
        ])
    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(['User ID', 'User', 'Email', 'Phone', 'Room', 'Joining', 'Leaving', 'Base', 'Source', 'DaysStayed/DaysInMonth', 'Expected'])
    for r in rows:
        w.writerow(r)
    resp = HttpResponse(sio.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="monthly-segments-{year}-{month:02d}.csv"'
    return resp


@login_required
def monthly_export_segments_user_csv(request, user_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    from django.contrib.auth import get_user_model
    User = get_user_model()
    u = get_object_or_404(User, pk=user_id)
    pg = _active_pg(request)
    # Prevent data exposure for residents unrelated to this PG
    if not _user_related_to_pg(u, pg):
        return HttpResponse("Forbidden: User not related to this PG.", status=403)
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, m_days = _month_range(year, month)
    qs = (
        Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg, user=u)
        .select_related('room', 'user__profile')
    )
    rows = []
    for b in qs:
        s = b.joining_date or b.start_date or b.created_at.date()
        e = b.leaving_date
        ov = _overlap_days(s, e, m_first, m_last)
        if ov <= 0:
            continue
        rr = ResidentRate.objects.filter(user=u, pg=pg, active=True).first()
        if rr:
            base = float(rr.amount)
            source = 'Custom rate'
        else:
            share_type = str(getattr(getattr(b, 'room', None), 'total_shares', '') or '')
            fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
            base = float(getattr(fees, 'monthly_fee', 0) or 0)
            source = f"{share_type}-Sharing fee" if share_type else 'Default fee'
        expected = _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
        phone_raw = getattr(getattr(b.user, 'profile', None), 'phone', '') or ''
        rows.append([
            getattr(b.room, 'room_no', ''),
            s.strftime('%Y-%m-%d') if s else '',
            e.strftime('%Y-%m-%d') if e else '',
            phone_raw,
            f"{base:.2f}", source, f"{ov}/{m_days}", f"{expected:.2f}",
        ])
    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(['Room', 'Joining', 'Leaving', 'Phone', 'Base', 'Source', 'DaysStayed/DaysInMonth', 'Expected'])
    for r in rows:
        w.writerow(r)
    resp = HttpResponse(sio.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="monthly-segments-{u.id}-{year}-{month:02d}.csv"'
    return resp


@login_required
def monthly_export_user_pdf(request, user_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    from django.contrib.auth import get_user_model
    User = get_user_model()
    u = get_object_or_404(User, pk=user_id)
    pg = _active_pg(request)
    if not _user_related_to_pg(u, pg):
        return HttpResponse("Forbidden: User not related to this PG.", status=403)
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, m_days = _month_range(year, month)
    # Gather segments like dashboard
    qs = (
        Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg, user=u)
        .select_related('room', 'user__profile')
    )
    segs = []
    for b in qs:
        s = b.joining_date or b.start_date or b.created_at.date()
        e = b.leaving_date
        ov = _overlap_days(s, e, m_first, m_last)
        if ov <= 0:
            continue
        rr = ResidentRate.objects.filter(user=u, pg=pg, active=True).first()
        if rr:
            base = float(rr.amount)
            source = 'Custom rate'
        else:
            share_type = str(getattr(getattr(b, 'room', None), 'total_shares', '') or '')
            fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
            base = float(getattr(fees, 'monthly_fee', 0) or 0)
            source = f"{share_type}-Sharing fee" if share_type else 'Default fee'
        expected = _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
        segs.append({
            'room_no': getattr(b.room, 'room_no', '—'),
            'start': s, 'end': e, 'base': base, 'source': source,
            'stayed': ov, 'expected': expected,
        })
    segs.sort(key=lambda x: (x['start'] or m_first, x['end'] or m_last))
    expected_total = round(sum(s['expected'] for s in segs), 2)
    collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
    pending = round(max(0.0, expected_total - collected), 2)

    # Build PDF (lazy import ReportLab to avoid hard dependency if not installed)
    rl_pagesizes = importlib.util.find_spec('reportlab.lib.pagesizes')
    rl_units = importlib.util.find_spec('reportlab.lib.units')
    rl_canvas = importlib.util.find_spec('reportlab.pdfgen.canvas')
    rl_colors = importlib.util.find_spec('reportlab.lib.colors')
    if not (rl_pagesizes and rl_units and rl_canvas and rl_colors):
        return HttpResponse("ReportLab not installed. Please install reportlab to enable PDF export.", status=400)
    pagesizes_mod = importlib.import_module('reportlab.lib.pagesizes')
    A4 = pagesizes_mod.A4
    landscape_fn = getattr(pagesizes_mod, 'landscape', None)
    mm = importlib.import_module('reportlab.lib.units').mm
    canvas = importlib.import_module('reportlab.pdfgen.canvas').Canvas
    colors = importlib.import_module('reportlab.lib.colors')
    resp = HttpResponse(content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="monthly-{u.id}-{year}-{month:02d}.pdf"'
    pagesize = landscape_fn(A4) if landscape_fn else A4
    p = canvas(resp, pagesize=pagesize)
    width, height = pagesize
    x_margin = 15 * mm
    y = height - 20 * mm
    p.setTitle(f"Monthly Summary {year}-{month:02d}")

    # Header
    p.setFont("Helvetica-Bold", 14)
    p.drawString(x_margin, y, f"Monthly Summary — {calendar.month_name[month]} {year}")
    y -= 8 * mm
    p.setFont("Helvetica", 11)
    p.drawString(x_margin, y, f"PG: {pg.name if pg else ''}")
    y -= 6 * mm
    resident_name = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email
    phone_raw = getattr(getattr(u, 'profile', None), 'phone', '') or ''
    p.drawString(x_margin, y, f"Resident: {resident_name}")
    y -= 6 * mm
    p.drawString(x_margin, y, f"Email: {u.email}")
    y -= 6 * mm
    if phone_raw:
        p.drawString(x_margin, y, f"Phone: {phone_raw}")
        y -= 6 * mm
    y -= 4 * mm

    # Summary cards
    for label, val in [("Expected", expected_total), ("Collected", float(collected)), ("Pending", pending)]:
        p.setFont("Helvetica-Bold", 12)
        p.drawString(x_margin, y, f"{label}:")
        p.setFont("Helvetica", 12)
        p.drawString(x_margin + 30 * mm, y, f"Rs. {val:.2f}")
        y -= 7 * mm
    y -= 3 * mm

    # Table header
    p.setFont("Helvetica-Bold", 11)
    p.drawString(x_margin, y, "Room")
    p.drawString(x_margin + 25 * mm, y, "Joining")
    p.drawString(x_margin + 60 * mm, y, "Leaving")
    p.drawString(x_margin + 95 * mm, y, "Base")
    p.drawString(x_margin + 120 * mm, y, "Days")
    p.drawString(x_margin + 140 * mm, y, "Expected")
    y -= 5 * mm
    p.setStrokeColor(colors.grey)
    p.line(x_margin, y, width - x_margin, y)
    y -= 5 * mm

    p.setFont("Helvetica", 10)
    for s in segs:
        if y < 25 * mm:
            p.showPage();
            try:
                p.setPageSize(pagesize)
            except Exception:
                pass
            y = height - 20 * mm
            p.setFont("Helvetica-Bold", 11)
            p.drawString(x_margin, y, "Room")
            p.drawString(x_margin + 25 * mm, y, "Joining")
            p.drawString(x_margin + 60 * mm, y, "Leaving")
            p.drawString(x_margin + 95 * mm, y, "Base")
            p.drawString(x_margin + 120 * mm, y, "Days")
            p.drawString(x_margin + 140 * mm, y, "Expected")
            y -= 5 * mm
            p.setStrokeColor(colors.grey)
            p.line(x_margin, y, width - x_margin, y)
            y -= 5 * mm
            p.setFont("Helvetica", 10)
        p.drawString(x_margin, y, str(s['room_no']))
        p.drawString(x_margin + 25 * mm, y, (s['start'] or m_first).strftime('%Y-%m-%d'))
        p.drawString(x_margin + 60 * mm, y, (s['end'].strftime('%Y-%m-%d') if s['end'] else '—'))
        p.drawRightString(x_margin + 115 * mm, y, f"Rs. {s['base']:.2f}")
        p.drawRightString(x_margin + 135 * mm, y, f"{int(s['stayed'])}/{m_days}")
        p.drawRightString(width - x_margin, y, f"Rs. {float(s['expected']):.2f}")
        y -= 6 * mm

    p.showPage()
    p.save()
    return resp


@login_required
def monthly_export_pdf(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    pg = _active_pg(request)
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, m_days = _month_range(year, month)

    # Sorting params (default to dashboard defaults)
    sort_key = (request.GET.get('sort') or 'room').strip().lower()
    sort_dir = (request.GET.get('dir') or 'asc').strip().lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'asc'

    # Reuse dashboard grouping logic
    active_bks = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg).select_related('user', 'room')
    by_user = {}
    for b in active_bks:
        s = b.joining_date or b.start_date or b.created_at.date()
        e = b.leaving_date
        ov = _overlap_days(s, e, m_first, m_last)
        if ov <= 0:
            continue
        by_user.setdefault(b.user_id, []).append(b)

    # Aggregate rows
    data = []
    total_expected = 0.0
    total_collected = 0.0
    only_filter = (request.GET.get('only') or '').strip().lower()
    from django.contrib.auth import get_user_model
    User = get_user_model()
    for user_id, bookings in by_user.items():
        bookings.sort(key=lambda b: (b.joining_date or b.start_date or b.created_at.date()))
        u = bookings[0].user
        segs = []
        expected_total = 0.0
        earliest = None
        latest = None
        due_date = None
        best_overlap = -1
        for b in bookings:
            s = b.joining_date or b.start_date or b.created_at.date()
            e = b.leaving_date
            ov = _overlap_days(s, e, m_first, m_last)
            if ov <= 0:
                continue
            rr = ResidentRate.objects.filter(user=u, pg=pg, active=True).first()
            if rr:
                base = float(rr.amount)
                source = 'Custom rate'
            else:
                share_type = str(getattr(getattr(b, 'room', None), 'total_shares', '') or '')
                fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
                base = float(getattr(fees, 'monthly_fee', 0) or 0)
                source = f"{share_type}-Sharing fee" if share_type else 'Default fee'
            exp = _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
            expected_total += exp
            earliest = min(earliest, s) if earliest else s
            if e:
                latest = max(latest, e) if latest else e
            segs.append({'room': getattr(b.room, 'room_no', '—'), 'start': s, 'end': e, 'base': base, 'source': source, 'days': ov, 'expected': exp})
            due_candidate = _payment_due_for_month(b, m_first, m_days)
            if due_candidate and ov > best_overlap:
                due_date = due_candidate
                best_overlap = ov
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        status, status_label, status_css = _resolve_status(expected_total, float(collected), m_first, due_date, today)
        if only_filter in ('paid', 'partial', 'unpaid', 'upcoming') and status != only_filter:
            continue
        phone_raw = getattr(getattr(u, 'profile', None), 'phone', '') or ''
        data.append({
            'user': u,
            'segments': segs,
            'expected': round(expected_total, 2),
            'collected': round(float(collected), 2),
            'pending': round(max(0.0, expected_total - collected), 2),
            'status': status,
            'status_label': status_label,
            'phone': phone_raw,
        })
        total_expected += expected_total
        total_collected += float(collected)

    # Sort tenant data like dashboard
    import re
    status_order = {'upcoming': -1, 'unpaid': 0, 'partial': 1, 'paid': 2}
    def _room_sort_val(room_no):
        s = str(room_no or '')
        m = re.search(r"\d+", s)
        if m:
            try:
                return (0, int(m.group()))
            except Exception:
                pass
        return (1, s.strip().lower())
    def _resident_name(u):
        name = f"{(getattr(u, 'first_name', '') or '').strip()} {(getattr(u, 'last_name', '') or '').strip()}".strip()
        return (name or getattr(u, 'email', '') or '').strip().lower()
    def _key(row):
        if sort_key == 'room':
            # Representative room: latest segment's room
            rep_room = None
            if row['segments']:
                rep_room = row['segments'][-1]['room']
            return _room_sort_val(rep_room)
        if sort_key in ('resident', 'user'):
            return _resident_name(row['user'])
        if sort_key == 'expected':
            return float(row.get('expected') or 0.0)
        if sort_key == 'collected':
            return float(row.get('collected') or 0.0)
        if sort_key == 'pending':
            return float(row.get('pending') or 0.0)
        if sort_key == 'status':
            # Derive status like dashboard
            exp = float(row.get('expected') or 0.0)
            col = float(row.get('collected') or 0.0)
            st = 'paid' if col >= exp - 0.5 else ('partial' if col > 0 else 'unpaid')
            return status_order.get(st, 99)
        if sort_key == 'joining':
            joins = [(s['start']) for s in row['segments'] if s.get('start')]
            j = min(joins) if joins else None
            return (j is None, j or m_first)
        if sort_key == 'leaving':
            leaves = [(s['end']) for s in row['segments'] if s.get('end')]
            l = max(leaves) if leaves else None
            return (l is None, l or m_first)
        return _room_sort_val(row['segments'][-1]['room'] if row['segments'] else None)

    data.sort(key=_key, reverse=(sort_dir == 'desc'))

    # Build PDF (lazy import ReportLab)
    rl_pagesizes = importlib.util.find_spec('reportlab.lib.pagesizes')
    rl_units = importlib.util.find_spec('reportlab.lib.units')
    rl_canvas = importlib.util.find_spec('reportlab.pdfgen.canvas')
    rl_colors = importlib.util.find_spec('reportlab.lib.colors')
    if not (rl_pagesizes and rl_units and rl_canvas and rl_colors):
        return HttpResponse("ReportLab not installed. Please install reportlab to enable PDF export.", status=400)
    pagesizes_mod = importlib.import_module('reportlab.lib.pagesizes')
    A4 = pagesizes_mod.A4
    landscape_fn = getattr(pagesizes_mod, 'landscape', None)
    mm = importlib.import_module('reportlab.lib.units').mm
    canvas = importlib.import_module('reportlab.pdfgen.canvas').Canvas
    colors = importlib.import_module('reportlab.lib.colors')
    resp = HttpResponse(content_type='application/pdf')
    # Filename format: pgname-month-year-monthly_payments.pdf
    pg_name = (pg.name if pg else 'PG')
    month_name = calendar.month_name[month]
    def _safe_name(s: str) -> str:
        return ''.join(c for c in s if c.isalnum() or c in (' ', '-', '_')).strip()
    filename = f"{_safe_name(pg_name)}-{_safe_name(month_name)}-{year}-monthly_payments.pdf"
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    pagesize = landscape_fn(A4) if landscape_fn else A4
    p = canvas(resp, pagesize=pagesize)
    width, height = pagesize
    x_margin = 12 * mm
    y = height - 18 * mm

    p.setTitle(f"Monthly Report {year}-{month:02d}")
    p.setFont("Helvetica-Bold", 14)
    p.drawString(x_margin, y, f"Monthly Report — {calendar.month_name[month]} {year}")
    y -= 7 * mm
    p.setFont("Helvetica", 11)
    p.drawString(x_margin, y, f"PG: {pg.name if pg else ''}")
    y -= 6 * mm
    if pg and getattr(pg, 'address', None):
        p.setFont("Helvetica", 10)
        p.drawString(x_margin, y, f"Address: {pg.address.splitlines()[0]}")
        y -= 5 * mm
    if pg and getattr(pg, 'phone', None):
        p.setFont("Helvetica", 10)
        p.drawString(x_margin, y, f"Phone: {pg.phone}")
        y -= 5 * mm
    # Generated timestamp
    p.setFont("Helvetica-Oblique", 9)
    p.drawString(x_margin, y, f"Generated on: {timezone.now().astimezone().strftime('%Y-%m-%d %H:%M')} ")
    y -= 8 * mm

    # Overall Summary Table (Metric | Amount (Rs.))
    p.setFont("Helvetica-Bold", 12)
    p.drawString(x_margin, y, "Overall Summary Table")
    y -= 6 * mm
    # Header
    p.setFont("Helvetica-Bold", 10)
    p.drawString(x_margin, y, "Metric")
    p.drawString(x_margin + 70 * mm, y, "Amount (Rs.)")
    y -= 4 * mm
    p.setStrokeColor(colors.lightgrey)
    p.line(x_margin, y, width - x_margin, y)
    y -= 5 * mm
    # Rows
    p.setFont("Helvetica", 10)
    tot_expected = round(total_expected, 2); tot_collected = round(total_collected, 2); tot_pending = round(max(0.0, total_expected - total_collected), 2)
    for metric, val in [("Expected", tot_expected), ("Collected", tot_collected), ("Pending", tot_pending)]:
        if y < 25 * mm:
            p.showPage(); p.setPageSize(pagesize); y = height - 18 * mm
            p.setFont("Helvetica-Bold", 12); p.drawString(x_margin, y, "Overall Summary Table"); y -= 6 * mm
            p.setFont("Helvetica-Bold", 10); p.drawString(x_margin, y, "Metric"); p.drawString(x_margin + 70 * mm, y, "Amount (Rs.)"); y -= 4 * mm
            p.setStrokeColor(colors.lightgrey); p.line(x_margin, y, width - x_margin, y); y -= 5 * mm
            p.setFont("Helvetica", 10)
        p.drawString(x_margin, y, metric)
        p.drawString(x_margin + 70 * mm, y, f"{val:.2f}")
        y -= 6 * mm
    y -= 8 * mm

    # Tenant-wise Details Table
    p.setFont("Helvetica-Bold", 12)
    p.drawString(x_margin, y, "Tenant-wise Details Table")
    y -= 6 * mm
    tenant_col_width = 70 * mm
    phone_col_width = 32 * mm
    phone_col_x = x_margin + tenant_col_width
    room_col_x = phone_col_x + phone_col_width
    joining_col_x = room_col_x + 25 * mm
    leaving_col_x = joining_col_x + 30 * mm
    days_col_x =  leaving_col_x + 30 * mm
    expected_col_x = days_col_x + 20 * mm
    collected_col_x = expected_col_x + 20 * mm
    pending_col_x = collected_col_x + 20 * mm
    tenant_name_max_chars = 55

    def _draw_tenant_table_header():
        nonlocal y
        p.setFont("Helvetica-Bold", 9)
        p.drawString(x_margin, y, "Tenant Name")
        p.drawString(phone_col_x, y, "Phone")
        p.drawString(room_col_x, y, "Room")
        p.drawString(joining_col_x, y, "Joining Date")
        p.drawString(leaving_col_x, y, "Leaving Date")
        p.drawString(days_col_x, y, "Days Stayed")
        p.drawString(expected_col_x, y, "Expected")
        p.drawString(collected_col_x, y, "Collected")
        p.drawString(pending_col_x, y, "Pending")
        y -= 4 * mm
        p.setStrokeColor(colors.lightgrey)
        p.line(x_margin, y, width - x_margin, y)
        y -= 5 * mm
        p.setFont("Helvetica", 9)

    _draw_tenant_table_header()
    base_row_font = 9
    min_row_font = 6
    # Flatten segments into rows
    def _tenant_display(u, phone):
        name = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip()
        return name or u.email
    for row in data:
        u = row['user']
        tenant_name = _tenant_display(u, row.get('phone'))
        if not row['segments']:
            continue
        for s in row['segments']:
            if y < 25 * mm:
                p.showPage(); p.setPageSize(pagesize); y = height - 18 * mm
                p.setFont("Helvetica-Bold", 8); p.drawString(x_margin, y, "Tenant-wise Details Table"); y -= 6 * mm
                _draw_tenant_table_header()
            # Adjust tenant name font size if it exceeds available width
            avail_width = tenant_col_width - 4 * mm
            font_size = base_row_font
            p.setFont("Helvetica", font_size)
            while font_size > min_row_font and p.stringWidth(tenant_name[:tenant_name_max_chars], "Helvetica", font_size) > avail_width:
                font_size -= 1
                p.setFont("Helvetica", font_size)
            p.drawString(x_margin, y, tenant_name[:tenant_name_max_chars])
            p.setFont("Helvetica", base_row_font)
            p.drawString(phone_col_x, y, (row.get('phone') or ''))
            p.drawString(room_col_x, y, str(s['room']))
            p.drawString(joining_col_x, y, (s['start'] or m_first).strftime('%Y-%m-%d'))
            p.drawString(leaving_col_x, y, (s['end'].strftime('%Y-%m-%d') if s['end'] else '—'))
            p.drawString(days_col_x, y, f"{int(s['days'])}/{m_days}")
            p.drawString(expected_col_x, y, f"Rs. {float(s['expected']):.2f}")
            p.drawString(collected_col_x, y, f"Rs. {float(row['collected']):.2f}")
            p.drawString(pending_col_x, y, f"Rs. {float(row['pending']):.2f}")
            # Row separator line
            p.setStrokeColor(colors.lightgrey)
            p.line(x_margin, y - 2 * mm, width - x_margin, y - 2 * mm)
            y -= 6 * mm

    p.showPage();
    try:
        p.setPageSize(pagesize)
    except Exception:
        pass
    p.save()
    return resp


@login_required
def monthly_export_excel(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    pg = _active_pg(request)
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, m_days = _month_range(year, month)
    # Lazy import openpyxl; if not available, return a clear error
    ox_spec = importlib.util.find_spec('openpyxl')
    if not ox_spec:
        return HttpResponse("Excel export requires 'openpyxl'. Please install it to enable .xlsx export.", status=400)
    openpyxl = importlib.import_module('openpyxl')
    get_column_letter = importlib.import_module('openpyxl.utils').get_column_letter

    # Sorting params (default to dashboard defaults)
    sort_key = (request.GET.get('sort') or 'room').strip().lower()
    sort_dir = (request.GET.get('dir') or 'asc').strip().lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'asc'

    # Group by user
    active_bks = (
        Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg)
        .select_related('user', 'room', 'user__profile')
    )
    by_user = {}
    for b in active_bks:
        s = b.joining_date or b.start_date or b.created_at.date()
        e = b.leaving_date
        ov = _overlap_days(s, e, m_first, m_last)
        if ov <= 0:
            continue
        by_user.setdefault(b.user_id, []).append(b)

    wb = openpyxl.Workbook()
    ws_overall = wb.active
    ws_overall.title = 'Overall Summary'
    ws_tenants = wb.create_sheet('Tenant-wise Details')

    only_filter = (request.GET.get('only') or '').strip().lower()

    # Build per-user entries with aggregates and segments
    entries = []
    for user_id, bookings in by_user.items():
        bookings_sorted = sorted(bookings, key=lambda b: (b.joining_date or b.start_date or b.created_at.date(), b.id))
        if not bookings_sorted:
            continue
        u = bookings_sorted[0].user
        segments = []
        expected_total = 0.0
        due_date = None
        best_overlap = -1
        for b in bookings_sorted:
            s = b.joining_date or b.start_date or b.created_at.date()
            e = b.leaving_date
            ov = _overlap_days(s, e, m_first, m_last)
            if ov <= 0:
                continue
            rr = ResidentRate.objects.filter(user=u, pg=pg, active=True).first()
            if rr:
                base = float(rr.amount)
            else:
                share_type = str(getattr(getattr(b, 'room', None), 'total_shares', '') or '')
                fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
                base = float(getattr(fees, 'monthly_fee', 0) or 0)
            expected_seg = _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
            segments.append({
                'room': getattr(b.room, 'room_no', ''),
                'start': s,
                'end': e,
                'base': base,
                'days': ov,
                'expected': expected_seg,
            })
            expected_total += expected_seg
            due_candidate = _payment_due_for_month(b, m_first, m_days)
            if due_candidate and ov > best_overlap:
                due_date = due_candidate
                best_overlap = ov
        if not segments:
            continue
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        status, status_label, status_css = _resolve_status(expected_total, float(collected), m_first, due_date, today)
        if only_filter in ('paid', 'partial', 'unpaid', 'upcoming') and status != only_filter:
            continue
        phone_raw = getattr(getattr(u, 'profile', None), 'phone', '') or ''
        segments.sort(key=lambda seg: (seg['start'] or m_first, seg['end'] or m_last))
        entries.append({
            'user': u,
            'bookings': bookings_sorted,
            'segments': segments,
            'expected': round(expected_total, 2),
            'collected': round(float(collected), 2),
            'pending': round(max(0.0, expected_total - collected), 2),
            'status': status,
            'status_label': status_label,
            'status_css': status_css,
            'phone': phone_raw,
        })

    import re
    def _room_sort_val(room_no):
        s = str(room_no or '')
        m = re.search(r"\d+", s)
        if m:
            try:
                return (0, int(m.group()))
            except Exception:
                pass
        return (1, s.strip().lower())

    status_order = {'upcoming': -1, 'unpaid': 0, 'partial': 1, 'paid': 2}
    def _resident_name(entry):
        u = entry['user']
        name = f"{(getattr(u, 'first_name', '') or '').strip()} {(getattr(u, 'last_name', '') or '').strip()}".strip()
        return (name or getattr(u, 'email', '') or '').strip().lower()

    def _entry_sort_key(entry):
        segments = entry['segments']
        rep_room = segments[-1]['room'] if segments else ''
        if sort_key == 'room':
            return _room_sort_val(rep_room)
        if sort_key in ('resident', 'user'):
            return _resident_name(entry)
        if sort_key == 'expected':
            return float(entry.get('expected') or 0.0)
        if sort_key == 'collected':
            return float(entry.get('collected') or 0.0)
        if sort_key == 'pending':
            return float(entry.get('pending') or 0.0)
        if sort_key == 'status':
            return status_order.get(entry.get('status'), 99)
        if sort_key == 'joining':
            joins = [seg['start'] for seg in segments if seg.get('start')]
            j = min(joins) if joins else None
            return (j is None, j or m_first)
        if sort_key == 'leaving':
            leaves = [seg['end'] for seg in segments if seg.get('end')]
            l = max(leaves) if leaves else None
            return (l is None, l or m_first)
        return _room_sort_val(rep_room)

    entries.sort(key=_entry_sort_key, reverse=(sort_dir == 'desc'))

    # Overall Summary (Metric | Amount (Rs.))
    total_expected = sum(entry['expected'] for entry in entries)
    total_collected = sum(entry['collected'] for entry in entries)
    ws_overall.append(['Metric', 'Amount (Rs.)'])
    ws_overall.append(['Expected', round(total_expected, 2)])
    ws_overall.append(['Collected', round(total_collected, 2)])
    ws_overall.append(['Pending', round(max(0.0, total_expected - total_collected), 2)])

    # Tenant-wise Details (Tenant Name, Room, Joining Date, Leaving Date, Base Rent, Days Stayed, Expected, Collected, Pending)
    ws_tenants.append(['Tenant Name', 'Phone', 'Room', 'Joining Date', 'Leaving Date', 'Base Rent', 'Days Stayed', 'Expected', 'Collected', 'Pending'])
    for entry in entries:
        u = entry['user']
        tenant_name = (f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email)
        phone_val = entry.get('phone') or ''
        for seg in entry['segments']:
            start = seg['start']
            end = seg['end']
            ws_tenants.append([
                tenant_name,
                phone_val,
                seg['room'],
                start.strftime('%Y-%m-%d') if start else '',
                end.strftime('%Y-%m-%d') if end else '',
                round(seg['base'], 2),
                f"{int(seg['days'])}/{m_days}",
                round(seg['expected'], 2),
                entry['collected'],
                round(max(0.0, entry['expected'] - entry['collected']), 2),
            ])

    # Auto-width a bit
    for ws in [ws_overall, ws_tenants]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                try:
                    max_len = max(max_len, len(str(cell.value)))
                except Exception:
                    pass
            limit = 60 if (ws.title == 'Tenant-wise Details' and col_letter == 'A') else 40
            ws.column_dimensions[col_letter].width = min(limit, max(12, max_len + 2))

    from io import BytesIO
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    resp = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # Filename format: pgname-month-year-monthly_payments.xlsx
    pg_name = (pg.name if pg else 'PG')
    month_name = calendar.month_name[month]
    def _safe_name(s: str) -> str:
        return ''.join(c for c in s if c.isalnum() or c in (' ', '-', '_')).strip()
    filename = f"{_safe_name(pg_name)}-{_safe_name(month_name)}-{year}-monthly_payments.xlsx"
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@login_required
def monthly_remind(request, user_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('finance_monthly')
    from django.contrib.auth import get_user_model
    pg = _active_pg(request)
    User = get_user_model()
    u = get_object_or_404(User, pk=user_id)
    if not _user_related_to_pg(u, pg):
        messages.error(request, "User not in the selected PG.")
        return redirect('finance_monthly')
    # Month context
    today = timezone.now().date()
    year = int(request.GET.get('year') or today.year)
    month = int(request.GET.get('month') or today.month)
    m_first, m_last, _ = _month_range(year, month)
    # Compute pending for email body
    b = Booking.objects.filter(user=u, room__pg=pg, status=Booking.APPROVED).select_related('room').first()
    expected = _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last) if b else 0.0
    collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
    pending = max(0.0, round(expected - collected, 2))
    # Email
    subject = f"Rent Reminder — {calendar.month_name[month]} {year}"
    body = (
        f"Dear {u.first_name or 'Resident'},\n\n"
        f"This is a reminder for your {calendar.month_name[month]} {year} rent at {pg.name}.\n"
        f"Expected: ₹{expected:.2f}\nCollected: ₹{collected:.2f}\nPending: ₹{pending:.2f}\n\n"
        f"If you've already paid, please ignore this message.\n\nRegards,\n{pg.name}"
    )
    try:
        from django.core.mail import send_mail
        send_mail(subject, body, None, [u.email], fail_silently=True)
    except Exception:
        pass
    # Log reminder
    ReminderLog.objects.create(by_user=request.user, to_user=u, pg=pg, method='email', subject=subject, message=body, for_month=m_first)
    messages.success(request, f"Reminder emailed to {u.email}.")
    return redirect('finance_monthly')


# =============== Ledger: Resident payment history ===============
@login_required
def ledger_view(request, user_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('finance_monthly')
    from django.contrib.auth import get_user_model
    User = get_user_model()
    pg = _active_pg(request)
    resident = get_object_or_404(User, pk=user_id)
    if not _user_related_to_pg(resident, pg):
        messages.error(request, "Resident not in the selected PG.")
        return redirect('finance_monthly')
    # Filters (removed UI): only apply if explicit params provided; default is all-time
    today = timezone.now().date()
    year = request.GET.get('year')
    month = request.GET.get('month')
    try:
        year = int(year) if year not in (None, "",) else None
    except Exception:
        year = None
    try:
        month = int(month) if month not in (None, "",) else None
    except Exception:
        month = None
    # Query payments/adjustments for that user & pg
    p_qs = Payment.objects.filter(user=resident, pg=pg, status='success').order_by('date', 'id')
    a_qs = Adjustment.objects.filter(user=resident, pg=pg).order_by('date', 'id')
    if month and year:
        p_qs = p_qs.filter(date__year=year, date__month=month)
        a_qs = a_qs.filter(date__year=year, date__month=month)
    elif year and not month:
        p_qs = p_qs.filter(date__year=year)
        a_qs = a_qs.filter(date__year=year)
    # Combine and compute running balance (credits positive, debits negative)
    items = []
    for p in p_qs:
        items.append({
            'date': p.date,
            'type': 'payment',
            'description': dict(Payment.TYPE_CHOICES).get(p.type, p.type),
            'credit': float(p.amount),
            'debit': 0.0,
        })
    for a in a_qs:
        if a.type in ('credit', 'deposit_deduction'):
            credit = float(a.amount)
            debit = 0.0
        else:
            credit = 0.0
            debit = float(a.amount)
        items.append({
            'date': a.date,
            'type': f'adjustment/{a.type}',
            'description': a.note or a.get_type_display(),
            'credit': credit,
            'debit': debit,
        })
    items.sort(key=lambda x: (x['date'] or today, x['type']))
    total_credit = sum(i['credit'] for i in items)
    total_debit = sum(i['debit'] for i in items)
    running = 0.0
    for i in items:
        running += i['credit'] - i['debit']
        i['balance'] = round(running, 2)

    # Expected dues for the month if specified
    dues = None
    if month and year:
        m_first, m_last, _ = _month_range(year, month)
        b = Booking.objects.filter(user=resident, room__pg=pg, status=Booking.APPROVED).select_related('room').first()
        expected = _expected_rent_for_user_pg_month(resident, pg, b, m_first, m_last) if b else 0.0
        collected = _collected_for_user_pg_month(resident, pg, m_first, m_last)
        dues = round(max(0.0, expected - collected), 2)

    # Collect booking history for this resident in this PG (current and past)
    all_bks = list(Booking.objects.filter(user=resident, room__pg=pg).select_related('room').order_by('-joining_date', '-start_date', '-created_at'))
    today_date = timezone.now().date()
    current_bks = []
    past_bks = []
    def _b_summary(b):
        start = b.joining_date or b.start_date or (b.created_at.date() if getattr(b, 'created_at', None) else None)
        end = b.leaving_date
        return {
            'room_no': getattr(getattr(b, 'room', None), 'room_no', ''),
            'start': start,
            'end': end,
            'status': getattr(b, 'status', ''),
            'shares': getattr(getattr(b, 'room', None), 'total_shares', ''),
        }
    for b in all_bks:
        start = b.joining_date or b.start_date or (b.created_at.date() if getattr(b, 'created_at', None) else None)
        end = b.leaving_date
        # Classify: current if started and not ended yet or ends today/future; otherwise past
        if start and (end is None or end >= today_date) and start <= today_date:
            current_bks.append(_b_summary(b))
        else:
            past_bks.append(_b_summary(b))
    # Bookings with submitted application (if any)
    app_bookings = []
    for b in all_bks:
        try:
            if getattr(b, 'application', None):
                app_bookings.append(b)
        except Exception:
            # Ignore missing one-to-one relations
            pass

    ctx = {
        'pg': pg,
        'resident': resident,
        'items': items,
        'totals': {
            'credit': round(total_credit, 2),
            'debit': round(total_debit, 2),
            'balance': round(running, 2),
        },
    'filter': {'year': year or '', 'month': month or ''},
        'dues': dues,
        'bookings': {
            'current': current_bks,
            'past': past_bks,
        },
        'app_bookings': app_bookings,
        'resident_details': {
            'username': getattr(resident, 'username', ''),
            'email': resident.email,
            'first_name': resident.first_name,
            'last_name': resident.last_name,
            'date_joined': getattr(resident, 'date_joined', None),
            'last_login': getattr(resident, 'last_login', None),
            'is_active': getattr(resident, 'is_active', True),
        },
        'pgs': list(_admin_pgs(request.user)),
    }
    return render(request, 'finance/ledger.html', ctx)


@login_required
def ledger_export_csv(request, user_id):
    # Replaced by PDF export. Keeping function name but changing implementation to generate PDF.
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse("Forbidden: Unauthorized PG.", status=403)
    from django.contrib.auth import get_user_model
    User = get_user_model()
    pg = _active_pg(request)
    resident = get_object_or_404(User, pk=user_id)
    if not _user_related_to_pg(resident, pg):
        return HttpResponse("Forbidden: User not related to this PG.", status=403)
    today = timezone.now().date()
    year_param = request.GET.get('year')
    month_param = request.GET.get('month')
    try:
        year = int(year_param) if year_param not in (None, "") else None
    except Exception:
        year = None
    try:
        month = int(month_param) if month_param not in (None, "") else None
    except Exception:
        month = None
    p_qs = Payment.objects.filter(user=resident, pg=pg, status='success').order_by('date', 'id')
    a_qs = Adjustment.objects.filter(user=resident, pg=pg).order_by('date', 'id')
    if month and year:
        p_qs = p_qs.filter(date__year=year, date__month=month)
        a_qs = a_qs.filter(date__year=year, date__month=month)
    elif year and not month:
        p_qs = p_qs.filter(date__year=year)
        a_qs = a_qs.filter(date__year=year)
    # Build ledger rows and running balance
    entries = []
    for p in p_qs:
        entries.append({
            'date': p.date,
            'type': 'payment',
            'description': dict(Payment.TYPE_CHOICES).get(p.type, p.type),
            'credit': float(p.amount),
            'debit': 0.0,
        })
    for a in a_qs:
        if a.type in ('credit', 'deposit_deduction'):
            credit = float(a.amount); debit = 0.0
        else:
            credit = 0.0; debit = float(a.amount)
        entries.append({
            'date': a.date,
            'type': f'adjustment/{a.type}',
            'description': a.note or a.get_type_display(),
            'credit': credit,
            'debit': debit,
        })
    entries.sort(key=lambda x: (x['date'] or today, x['type']))
    bal = 0.0
    for e in entries:
        bal += e['credit'] - e['debit']
        e['balance'] = round(bal, 2)

    # Lazy import ReportLab; error if unavailable
    rl_pagesizes = importlib.util.find_spec('reportlab.lib.pagesizes')
    rl_units = importlib.util.find_spec('reportlab.lib.units')
    rl_canvas = importlib.util.find_spec('reportlab.pdfgen.canvas')
    rl_colors = importlib.util.find_spec('reportlab.lib.colors')
    if not (rl_pagesizes and rl_units and rl_canvas and rl_colors):
        return HttpResponse("ReportLab not installed. Please install reportlab to enable PDF export.", status=400)
    A4 = importlib.import_module('reportlab.lib.pagesizes').A4
    mm = importlib.import_module('reportlab.lib.units').mm
    canvas = importlib.import_module('reportlab.pdfgen.canvas').Canvas
    colors = importlib.import_module('reportlab.lib.colors')

    # Build filename username-pgname - payments.pdf (use username explicitly per requirement)
    display_name = (resident.username or f"user-{resident.id}")
    pg_name = (pg.name if pg else 'PG').strip()
    safe = lambda s: ''.join(c for c in s if c.isalnum() or c in (' ', '-', '_', '@', '.')).strip()
    filename = f"{safe(display_name)}-{safe(pg_name)} - payments.pdf"
    resp = HttpResponse(content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    pagesize = A4
    p = canvas(resp, pagesize=pagesize)
    width, height = pagesize
    x_margin = 15 * mm
    y = height - 20 * mm
    p.setTitle(f"Payments Ledger" + (f" {year}-{str(month).zfill(2)}" if (year and month) else (f" {year}" if year else "")))

    # Header
    p.setFont("Helvetica-Bold", 14)
    title = "Payments Ledger"
    if month and year:
        title = f"Payments Ledger — {calendar.month_name[month]} {year}"
    elif year and not month:
        title = f"Payments Ledger — {year}"
    p.drawString(x_margin, y, title)
    y -= 8 * mm
    p.setFont("Helvetica", 11)
    p.drawString(x_margin, y, f"Resident: {display_name} ({resident.email})")
    y -= 6 * mm
    p.drawString(x_margin, y, f"PG: {pg_name}")
    y -= 6 * mm

    # Resident details block
    name_str = f"{(resident.first_name or '').strip()} {(resident.last_name or '').strip()}".strip() or display_name
    p.setFont("Helvetica-Bold", 11)
    p.drawString(x_margin, y, "Resident Details")
    y -= 5 * mm
    p.setFont("Helvetica", 10)
    p.drawString(x_margin, y, f"Name: {name_str}")
    y -= 5 * mm
    p.drawString(x_margin, y, f"Username: {getattr(resident, 'username', '')}")
    y -= 5 * mm
    p.drawString(x_margin, y, f"Email: {resident.email}")
    y -= 5 * mm
    try:
        joined = getattr(resident, 'date_joined', None)
        if joined:
            p.drawString(x_margin, y, f"Joined: {joined.astimezone().strftime('%Y-%m-%d %H:%M')}")
            y -= 5 * mm
    except Exception:
        pass
    try:
        last_login = getattr(resident, 'last_login', None)
        if last_login:
            p.drawString(x_margin, y, f"Last login: {last_login.astimezone().strftime('%Y-%m-%d %H:%M')}")
            y -= 5 * mm
    except Exception:
        pass
    is_active = getattr(resident, 'is_active', True)
    p.drawString(x_margin, y, f"Active: {'Yes' if is_active else 'No'}")
    y -= 6 * mm

    # Current bookings summary for this PG
    p.setFont("Helvetica-Bold", 11)
    p.drawString(x_margin, y, "Current Booking(s)")
    y -= 5 * mm
    p.setFont("Helvetica", 10)
    try:
        all_bks = list(Booking.objects.filter(user=resident, room__pg=pg).select_related('room').order_by('-joining_date', '-start_date', '-created_at'))
        today_date = timezone.now().date()
        current_lines = []
        for b in all_bks:
            start = b.joining_date or b.start_date or (b.created_at.date() if getattr(b, 'created_at', None) else None)
            end = b.leaving_date
            if start and (end is None or end >= today_date) and start <= today_date:
                room_no = getattr(getattr(b, 'room', None), 'room_no', '')
                shares = getattr(getattr(b, 'room', None), 'total_shares', '')
                status = getattr(b, 'status', '')
                start_s = start.strftime('%Y-%m-%d') if start else '—'
                end_s = end.strftime('%Y-%m-%d') if end else '—'
                current_lines.append(f"Room {room_no} • {start_s} → {end_s} • {status} • {shares}-Sharing")
        if not current_lines:
            p.drawString(x_margin, y, "None")
            y -= 5 * mm
        else:
            for line in current_lines:
                if y < 30 * mm:
                    p.showPage()
                    try:
                        p.setPageSize(pagesize)
                    except Exception:
                        pass
                    y = height - 20 * mm
                p.drawString(x_margin, y, line)
                y -= 5 * mm
    except Exception:
        # Booking info is optional; ignore errors to not block PDF export
        p.drawString(x_margin, y, "—")
        y -= 5 * mm

    y -= 5 * mm

    # Table header
    p.setFont("Helvetica-Bold", 10)
    p.drawString(x_margin, y, "Date")
    p.drawString(x_margin + 24 * mm, y, "Type")
    p.drawString(x_margin + 60 * mm, y, "Description")
    p.drawRightString(width - 70 * mm, y, "Credit")
    p.drawRightString(width - 40 * mm, y, "Debit")
    p.drawRightString(width - x_margin, y, "Balance")
    y -= 4 * mm
    p.setStrokeColor(colors.grey)
    p.line(x_margin, y, width - x_margin, y)
    y -= 5 * mm

    p.setFont("Helvetica", 10)
    if not entries:
        p.drawString(x_margin, y, "No entries for the selected period.")
        y -= 6 * mm
    else:
        for e in entries:
            if y < 25 * mm:
                p.showPage()
                try:
                    p.setPageSize(pagesize)
                except Exception:
                    pass
                y = height - 20 * mm
                # Re-draw table header on new page
                p.setFont("Helvetica-Bold", 10)
                p.drawString(x_margin, y, "Date")
                p.drawString(x_margin + 24 * mm, y, "Type")
                p.drawString(x_margin + 60 * mm, y, "Description")
                p.drawRightString(width - 70 * mm, y, "Credit")
                p.drawRightString(width - 40 * mm, y, "Debit")
                p.drawRightString(width - x_margin, y, "Balance")
                y -= 4 * mm
                p.setStrokeColor(colors.grey)
                p.line(x_margin, y, width - x_margin, y)
                y -= 5 * mm
                p.setFont("Helvetica", 10)
            # Row content
            p.drawString(x_margin, y, (e['date'] or today).strftime('%Y-%m-%d'))
            p.drawString(x_margin + 24 * mm, y, e['type'])
            p.drawString(x_margin + 60 * mm, y, (e['description'] or '')[:50])
            p.drawRightString(width - 70 * mm, y, (f"Rs. {e['credit']:.2f}" if e['credit'] else ''))
            p.drawRightString(width - 40 * mm, y, (f"Rs. {e['debit']:.2f}" if e['debit'] else ''))
            p.drawRightString(width - x_margin, y, f"Rs. {e['balance']:.2f}")
            y -= 6 * mm

    # Footer totals
    total_credit = sum(e['credit'] for e in entries)
    total_debit = sum(e['debit'] for e in entries)
    if y < 30 * mm:
        p.showPage()
        try:
            p.setPageSize(pagesize)
        except Exception:
            pass
        y = height - 20 * mm
    p.setFont("Helvetica-Bold", 11)
    p.drawRightString(width - 70 * mm, y, f"Rs. {total_credit:.2f}")
    p.drawRightString(width - 40 * mm, y, f"Rs. {total_debit:.2f}")
    p.drawRightString(width - x_margin, y, f"Rs. {(total_credit - total_debit):.2f}")

    p.showPage(); p.save()
    return resp

@login_required
def ledger_export_pdf(request, user_id):
    # For backward-compatibility, route to the same implementation as ledger_export_csv now does (PDF)
    return ledger_export_csv(request, user_id)


@login_required
def monthly_bulk_remind(request):
    if request.method != 'POST':
        return redirect('finance_monthly')
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    req_pg = request.POST.get('pg') or request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        messages.error(request, "You do not have access to the requested PG.")
        return redirect('finance_monthly')
    pg = _active_pg(request)
    ids = request.POST.getlist('user_ids')
    from django.contrib.auth import get_user_model
    User = get_user_model()
    users = list(User.objects.filter(id__in=ids))
    # Filter to users related to this PG only
    users = [u for u in users if _user_related_to_pg(u, pg)]
    today = timezone.now().date()
    year = int(request.POST.get('year') or today.year)
    month = int(request.POST.get('month') or today.month)
    m_first, m_last, _ = _month_range(year, month)
    # Build one email BCC
    subject = f"Rent Reminder — {calendar.month_name[month]} {year}"
    body = (
        f"This is a gentle reminder for your {calendar.month_name[month]} {year} rent at {pg.name}.\n"
        f"Please complete any pending payments at the earliest.\n\nRegards,\n{pg.name}"
    )
    try:
        from django.core.mail import EmailMessage
        msg = EmailMessage(subject, body, None, bcc=[u.email for u in users])
        msg.send(fail_silently=True)
    except Exception:
        pass
    for u in users:
        ReminderLog.objects.create(by_user=request.user, to_user=u, pg=pg, method='email', subject=subject, message=body, for_month=m_first)
    messages.success(request, f"Bulk reminder emailed to {len(users)} resident(s) in this PG.")
    return redirect('finance_monthly')


@login_required
def monthly_quick_payment(request):
    """Create a quick payment from the monthly dashboard modal and email the resident.
    Expected POST fields: user_id, pg, amount, mode (upi|cash|bank), type (fee|advance), optional notes.
    Preserves month context on redirect via year, month, sort, dir, only.
    """
    if request.method != 'POST':
        return redirect('finance_monthly')
    if not _require_pg_admin(request.user):
        messages.error(request, 'PG Admin access required.')
        return redirect('dashboard')

    # Context params for redirect
    year = request.POST.get('year'); month = request.POST.get('month')
    only = request.POST.get('only')
    sort_key = request.POST.get('sort'); sort_dir = request.POST.get('dir')
    req_pg = request.POST.get('pg') or request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return HttpResponse('Forbidden: Unauthorized PG.', status=403)
    pg = _active_pg(request)
    # If explicit pg provided and user has access, prefer it
    if req_pg and str(getattr(pg, 'id', '')) != str(req_pg):
        try:
            from pgadmin.models import PG as PGModel
            pg = PGModel.objects.get(pk=req_pg)
        except Exception:
            pass

    # Validate inputs
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        u = User.objects.get(pk=int(request.POST.get('user_id') or 0))
    except Exception:
        messages.error(request, 'Invalid resident selected.')
        return redirect('finance_monthly')
    if not _user_related_to_pg(u, pg):
        return HttpResponse('Forbidden: Resident not in selected PG.', status=403)
    raw_amount = (request.POST.get('amount') or '').strip()
    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        messages.error(request, 'Invalid amount.')
        return redirect('finance_monthly')
    if amount <= 0:
        messages.error(request, 'Amount should be greater than 0.')
        return redirect('finance_monthly')
    mode = (request.POST.get('mode') or 'upi').lower()
    if mode not in ('upi', 'cash', 'bank'):
        mode = 'upi'
    ptype = (request.POST.get('type') or 'fee').lower()
    if ptype not in ('fee', 'advance'):
        ptype = 'fee'
    notes = (request.POST.get('notes') or '').strip()
    # Optional date override
    date_str = (request.POST.get('date') or '').strip()
    pay_date = None
    if date_str:
        try:
            pay_date = parse_date(date_str)
        except Exception:
            pay_date = None
    if not pay_date:
        pay_date = timezone.now().date()

    # Create Payment (success by default)
    try:
        Payment.objects.create(
            user=u, pg=pg, amount=amount, date=pay_date,
            status='success', mode=mode, type=ptype, notes=notes,
        )
    except Exception as e:
        messages.error(request, f'Failed to create payment: {e}')
        # AJAX error
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
            return JsonResponse({'ok': False, 'message': f'Failed to create payment: {e}'})
        return redirect('finance_monthly')

    # Send HTML receipt email using shared template (best-effort)
    try:
        # Build context for email template
        tenant_name = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email
        # Room number from the most recent/current booking in this PG
        room_no = ''
        try:
            latest_b = Booking.objects.filter(user=u, room__pg=pg).select_related('room').order_by('-joining_date', '-start_date', '-created_at').first()
            room_no = getattr(getattr(latest_b, 'room', None), 'room_no', '') or ''
        except Exception:
            pass
        # Phone numbers
        pg_phone = getattr(pg, 'phone', '') or ''

        # Sanitize WhatsApp phone: digits only, include country code (default to +91 if 10 digits)
        try:
            import re
            raw = pg_phone
            digits = re.sub(r"\D", "", raw or "")
            # handle common India patterns: leading 0, 10-digit local
            if digits.startswith('0') and len(digits) > 1:
                digits = digits.lstrip('0')
            if len(digits) == 10:
                digits = '91' + digits
            whatsapp_phone = digits
        except Exception:
            whatsapp_phone = ''
        # Address short (first line)
        addr = getattr(pg, 'address', '') or ''
        pg_address_short = (addr.splitlines()[0] if addr else '')
        # Payment details display
        payment_type_disp = 'Fee' if ptype == 'fee' else 'Advance'
        payment_mode_disp = {'upi': 'UPI', 'cash': 'Cash', 'bank': 'Bank Transfer'}.get(mode, mode.title())
        payment_date_disp = pay_date.strftime('%Y-%m-%d')
        ctx = {
            'tenant_name': tenant_name,
            'pg_name': getattr(pg, 'name', '') or 'PG',
            'room_number': room_no,
            'payment_date': payment_date_disp,
            'payment_type': payment_type_disp,
            'payment_method': payment_mode_disp,
            'amount_paid': f"{amount:.2f}",
            'pg_phone': pg_phone,
            'whatsapp_phone': whatsapp_phone,
            'current_year': timezone.now().year,
            'pg_address_short': pg_address_short,
        }
        html = render_to_string('email/payments/receipt.html', ctx)
        text = strip_tags(html)
        subject = 'Payment Receipt'
        msg = EmailMultiAlternatives(subject, text, to=[u.email])
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=True)
    except Exception:
        pass

    # Compute updated monthly metrics for this user
    # Determine month context for metrics; fallback to payment date month if not provided
    try:
        y = int(year) if year else pay_date.year
    except Exception:
        y = pay_date.year
    try:
        m = int(month) if month else pay_date.month
    except Exception:
        m = pay_date.month
    m_first, m_last, m_days = _month_range(y, m)

    # Expected across overlapping bookings in the month
    expected_total = 0.0
    primary_due = None
    primary_overlap = -1
    user_bookings = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg, user=u).select_related('room')
    for b in user_bookings:
        s = b.joining_date or b.start_date or b.created_at.date()
        e = b.leaving_date
        overlap = _overlap_days(s, e, m_first, m_last)
        if overlap > 0:
            expected_total += _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
            due_candidate = _payment_due_for_month(b, m_first, m_days)
            if due_candidate and overlap > primary_overlap:
                primary_due = due_candidate
                primary_overlap = overlap
    expected_total = float(round(expected_total, 2))
    collected_total = float(round(_collected_for_user_pg_month(u, pg, m_first, m_last), 2))
    pending_total = float(round(max(0.0, expected_total - collected_total), 2))
    new_status, new_status_label, new_status_css = _resolve_status(expected_total, collected_total, m_first, primary_due, timezone.now().date())

    # Recompute overall summary totals for the selected PG and month (respecting filter 'only' if provided)
    # Build resident rows data similar to monthly_dashboard but only gathering totals
    active_bks = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg).select_related('user', 'room')
    by_user = {}
    for b in active_bks:
        s = b.joining_date or b.start_date or b.created_at.date()
        e = b.leaving_date
        if _overlap_days(s, e, m_first, m_last) <= 0:
            continue
        by_user.setdefault(b.user_id, []).append(b)
    totals_expected = 0.0
    totals_collected = 0.0
    totals_advance = 0.0
    # Precompute total advance across all users in this PG
    try:
        adv_qs = Payment.objects.filter(pg=pg, status='success', type='advance')
        totals_advance = float(adv_qs.aggregate(total=Sum('amount')).get('total') or 0.0)
    except Exception:
        totals_advance = 0.0
    # Calculate expected/collected across users, with optional status filter
    only_filter = (only or '').strip().lower()
    for user_id, bookings in by_user.items():
        u2 = bookings[0].user
        exp_u = 0.0
        due_u = None
        best_overlap = -1
        for b in bookings:
            s = b.joining_date or b.start_date or b.created_at.date()
            e = b.leaving_date
            overlap = _overlap_days(s, e, m_first, m_last)
            if overlap > 0:
                exp_u += _expected_rent_for_user_pg_month(u2, pg, b, m_first, m_last)
                due_candidate = _payment_due_for_month(b, m_first, m_days)
                if due_candidate and overlap > best_overlap:
                    due_u = due_candidate
                    best_overlap = overlap
        col_u = _collected_for_user_pg_month(u2, pg, m_first, m_last)
        status_u, _, _ = _resolve_status(float(exp_u), float(col_u), m_first, due_u, timezone.now().date())
        if only_filter in ('paid', 'partial', 'unpaid', 'upcoming') and status_u != only_filter:
            continue
        totals_expected += exp_u
        totals_collected += float(col_u)
    totals_pending = max(0.0, totals_expected - totals_collected)

    # AJAX response
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
        return JsonResponse({
            'ok': True,
            'message': f'Payment of ₹{amount:.2f} added for {u.email}.',
            'user_id': u.id,
            'expected': f"{expected_total:.2f}",
            'collected': f"{collected_total:.2f}",
            'pending': f"{pending_total:.2f}",
            'collected_display': f"{collected_total:.2f}",
            'pending_display': f"{pending_total:.2f}",
            'status': new_status,
            'status_label': new_status_label,
            'status_css': new_status_css,
            # Overall cards
            'sum_expected': f"{totals_expected:.2f}",
            'sum_collected': f"{totals_collected:.2f}",
            'sum_pending': f"{totals_pending:.2f}",
            'sum_advance': f"{totals_advance:.2f}",
        })

    # Non-AJAX: flash and redirect back with context
    messages.success(request, f'Payment of ₹{amount:.2f} added for {u.email}.')
    from django.urls import reverse
    base = reverse('finance_monthly')
    params = []
    if year: params.append(f'year={year}')
    if month: params.append(f'month={month}')
    if only: params.append(f'only={only}')
    if sort_key: params.append(f'sort={sort_key}')
    if sort_dir: params.append(f'dir={sort_dir}')
    if pg: params.append(f'pg={pg.id}')
    q = ('?' + '&'.join(params)) if params else ''
    return redirect(base + q)
