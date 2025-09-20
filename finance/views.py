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
from django.http import HttpResponse
from io import StringIO
import csv
from .models import ResidentRate, ReminderLog, Adjustment
import importlib


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


def _expected_rent_for_user_pg_month(u, pg, booking, m_first, m_last) -> float:
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
    return round((monthly * stayed) / days_in_month, 2)


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
            segs.append({
                'b': b, 'start': s, 'end': e, 'base': base_monthly, 'source': base_source,
                'expected': exp_part, 'stayed': stayed,
                'room_no': getattr(b.room, 'room_no', '—')
            })
        segs.sort(key=lambda x: (x['start'] or m_first, x['end'] or m_last))

        expected_total = round(sum((seg['expected'] or 0.0) for seg in segs), 2)
        # Collected for the user across the month (unchanged)
        u = segs[0]['b'].user
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        pending = round(expected_total - collected, 2)
        # Status classification
        if collected >= expected_total - 0.5:
            status = 'paid'
        elif collected > 0:
            status = 'partial'
        else:
            status = 'unpaid'
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
        # Pick joining as earliest start in month; leaving as latest end if present
        earliest_start = min((seg['start'] or m_first) for seg in segs)
        latest_end = None
        ends = [seg['end'] for seg in segs if seg['end']]
        if ends:
            latest_end = max(ends)
        # Choose a representative room_no (latest segment's room)
        last_seg = sorted(segs, key=lambda x: (x['start'] or m_first, x['end'] or m_last))[-1]
        rows.append({
            'user': u,
            'room_no': getattr(last_seg['b'].room, 'room_no', '—'),
            'expected': expected_total,
            'expected_tip': exp_tip,
            'collected': round(float(collected), 2),
            'pending': max(0.0, pending),
            'status': status,
            'joining': earliest_start,
            'leaving': latest_end,
            'whatsapp_phone': digits,
            'advance': round(_advance_paid_for_user_pg(u, pg), 2),
            'segments': [
                {
                    'room_no': seg['room_no'],
                    'joining': seg['start'],
                    'leaving': seg['end'],
                    'base': seg['base'],
                    'source': seg['source'],
                    'stayed': seg['stayed'],
                    'expected': seg['expected'],
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
    if only in ('paid', 'unpaid', 'partial'):
        rows = [r for r in rows if r['status'] == only]

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
    })


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
    m_first, m_last, _ = _month_range(year, month)

    # Build rows like dashboard by summing all overlapping stays per user
    active_bks = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg).select_related('user', 'room')
    by_user = {}
    for b in active_bks:
        start = b.joining_date or b.start_date or b.created_at.date()
        end = b.leaving_date
        ov = _overlap_days(start, end, m_first, m_last)
        if ov <= 0:
            continue
        by_user.setdefault(b.user_id, []).append(b)
    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(['User', 'Email', 'Room', 'Joining', 'Advance', 'Expected', 'Collected', 'Pending', 'Status'])
    for user_id, bookings in by_user.items():
        bookings.sort(key=lambda b: (b.joining_date or b.start_date or b.created_at.date()))
        u = bookings[0].user
        expected = 0.0
        earliest_start = None
        latest_end = None
        for b in bookings:
            expected += _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
            s = b.joining_date or b.start_date or b.created_at.date()
            e = b.leaving_date
            earliest_start = min(earliest_start, s) if earliest_start else s
            if e:
                latest_end = max(latest_end, e) if latest_end else e
        expected = round(expected, 2)
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        pending = round(expected - collected, 2)
        if collected >= expected - 0.5:
            status = 'paid'
        elif collected > 0:
            status = 'partial'
        else:
            status = 'unpaid'
        advance = _advance_paid_for_user_pg(u, pg)
        w.writerow([
            f"{u.first_name} {u.last_name}".strip() or u.email,
            u.email,
            getattr(bookings[-1].room, 'room_no', ''),
            (earliest_start.strftime('%Y-%m-%d') if earliest_start else ''),
            f"{float(advance):.2f}",
            f"{expected:.2f}", f"{float(collected):.2f}", f"{max(0.0, pending):.2f}", status
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
    active_bks = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg).select_related('user', 'room')
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
        rows.append([
            b.user.id,
            f"{b.user.first_name} {b.user.last_name}".strip() or b.user.email,
            b.user.email,
            getattr(b.room, 'room_no', ''),
            s.strftime('%Y-%m-%d') if s else '',
            e.strftime('%Y-%m-%d') if e else '',
            f"{base:.2f}", source, f"{ov}/{m_days}", f"{expected:.2f}",
        ])
    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(['User ID', 'User', 'Email', 'Room', 'Joining', 'Leaving', 'Base', 'Source', 'DaysStayed/DaysInMonth', 'Expected'])
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
    qs = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg, user=u).select_related('room')
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
        rows.append([
            getattr(b.room, 'room_no', ''),
            s.strftime('%Y-%m-%d') if s else '',
            e.strftime('%Y-%m-%d') if e else '',
            f"{base:.2f}", source, f"{ov}/{m_days}", f"{expected:.2f}",
        ])
    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(['Room', 'Joining', 'Leaving', 'Base', 'Source', 'DaysStayed/DaysInMonth', 'Expected'])
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
    qs = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg, user=u).select_related('room')
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
    p.drawString(x_margin, y, f"Resident: {(u.first_name + ' ' + u.last_name).strip() or u.email}")
    y -= 6 * mm
    p.drawString(x_margin, y, f"Email: {u.email}")
    y -= 10 * mm

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
    from django.contrib.auth import get_user_model
    User = get_user_model()
    for user_id, bookings in by_user.items():
        bookings.sort(key=lambda b: (b.joining_date or b.start_date or b.created_at.date()))
        u = bookings[0].user
        segs = []
        expected_total = 0.0
        earliest = None
        latest = None
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
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        data.append({'user': u, 'segments': segs, 'expected': round(expected_total, 2), 'collected': round(float(collected), 2), 'pending': round(max(0.0, expected_total - collected), 2)})
        total_expected += expected_total
        total_collected += float(collected)

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
    # Header: Tenant Name | Room | Joining Date | Leaving Date | Base Rent | Days Stayed | Expected | Collected | Pending
    p.setFont("Helvetica-Bold", 9)
    p.drawString(x_margin, y, "Tenant Name")
    p.drawString(x_margin + 45 * mm, y, "Room")
    p.drawString(x_margin + 60 * mm, y, "Joining Date")
    p.drawString(x_margin + 90 * mm, y, "Leaving Date")
    p.drawString(x_margin + 120 * mm, y, "Base Rent")
    p.drawString(x_margin + 145 * mm, y, "Days Stayed")
    # right-aligned monetary columns
    # Left-align headers for monetary columns like other columns
    p.drawString(width - 80 * mm, y, "Expected")
    p.drawString(width - 55 * mm, y, "Collected")
    p.drawString(width - x_margin - 25 * mm, y, "Pending")
    y -= 4 * mm
    p.setStrokeColor(colors.lightgrey)
    p.line(x_margin, y, width - x_margin, y)
    y -= 5 * mm
    p.setFont("Helvetica", 9)
    # Flatten segments into rows
    def _tenant_display(u):
        name = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip()
        return name or u.email
    for row in data:
        u = row['user']
        tenant_name = _tenant_display(u)
        if not row['segments']:
            continue
        for s in row['segments']:
            if y < 25 * mm:
                p.showPage(); p.setPageSize(pagesize); y = height - 18 * mm
                p.setFont("Helvetica-Bold", 12); p.drawString(x_margin, y, "Tenant-wise Details Table"); y -= 6 * mm
                p.setFont("Helvetica-Bold", 9)
                p.drawString(x_margin, y, "Tenant Name")
                p.drawString(x_margin + 45 * mm, y, "Room")
                p.drawString(x_margin + 60 * mm, y, "Joining Date")
                p.drawString(x_margin + 90 * mm, y, "Leaving Date")
                p.drawString(x_margin + 120 * mm, y, "Base Rent")
                p.drawString(x_margin + 140 * mm, y, "Days Stayed")
                # Left-align headers for monetary columns like other columns
                p.drawString(width - 80 * mm, y, "Expected")
                p.drawString(width - 55 * mm, y, "Collected")
                p.drawString(width - x_margin - 25 * mm, y, "Pending")
                y -= 4 * mm
                p.setStrokeColor(colors.lightgrey)
                p.line(x_margin, y, width - x_margin, y)
                y -= 5 * mm
                p.setFont("Helvetica", 9)
            p.drawString(x_margin, y, tenant_name[:38])
            p.drawString(x_margin + 45 * mm, y, str(s['room']))
            p.drawString(x_margin + 60 * mm, y, (s['start'] or m_first).strftime('%Y-%m-%d'))
            p.drawString(x_margin + 90 * mm, y, (s['end'].strftime('%Y-%m-%d') if s['end'] else '—'))
            p.drawRightString(x_margin + 140 * mm, y, f"Rs. {float(s['base']):.2f}")
            p.drawString(x_margin + 145 * mm, y, f"{int(s['days'])}/{m_days}")
            # Left-align values to match header alignment
            p.drawString(width - 80 * mm, y, f"Rs. {float(s['expected']):.2f}")
            p.drawString(width - 55 * mm, y, f"Rs. {float(row['collected']):.2f}")
            p.drawString(width - x_margin - 25 * mm, y, f"Rs. {float(row['pending']):.2f}")
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

    # Group by user
    active_bks = Booking.objects.filter(status__in=[Booking.APPROVED, Booking.COMPLETED], room__pg=pg).select_related('user', 'room')
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

    # Overall Summary (Metric | Amount (Rs.))
    total_expected = 0.0
    total_collected = 0.0
    for user_id, bookings in by_user.items():
        bookings.sort(key=lambda b: (b.joining_date or b.start_date or b.created_at.date()))
        u = bookings[0].user
        expected_total = 0.0
        for b in bookings:
            expected_total += _expected_rent_for_user_pg_month(u, pg, b, m_first, m_last)
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        total_expected += expected_total
        total_collected += float(collected)
    ws_overall.append(['Metric', 'Amount (Rs.)'])
    ws_overall.append(['Expected', round(total_expected, 2)])
    ws_overall.append(['Collected', round(total_collected, 2)])
    ws_overall.append(['Pending', round(max(0.0, total_expected - total_collected), 2)])

    # Tenant-wise Details (Tenant Name, Room, Joining Date, Leaving Date, Base Rent, Days Stayed, Expected, Collected, Pending)
    ws_tenants.append(['Tenant Name', 'Room', 'Joining Date', 'Leaving Date', 'Base Rent', 'Days Stayed', 'Expected', 'Collected', 'Pending'])
    from django.contrib.auth import get_user_model
    User = get_user_model()
    for user_id, bookings in by_user.items():
        u = bookings[0].user
        # Compute collected/pending once per tenant for this month
        collected_u = _collected_for_user_pg_month(u, pg, m_first, m_last)
        expected_total_u = 0.0
        for b in bookings:
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
            expected_total_u += expected_seg
            tenant_name = (f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email)
            ws_tenants.append([
                tenant_name,
                getattr(b.room, 'room_no', ''),
                s.strftime('%Y-%m-%d') if s else '',
                e.strftime('%Y-%m-%d') if e else '',
                round(base, 2),
                f"{int(ov)}/{m_days}",
                round(expected_seg, 2),
                round(float(collected_u), 2),
                round(max(0.0, expected_total_u - collected_u), 2),
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
            ws.column_dimensions[col_letter].width = min(40, max(12, max_len + 2))

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
