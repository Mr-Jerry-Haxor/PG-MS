from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model

from pgadmin.models import PG
from .models import Fees, Payment, Expenditure, ExpenditureCategory, MonthlyAdjustment
from core.audit import log
from .forms import FeesForm, PaymentForm, ExpenditureForm
from bookings.models import Booking, ReferralCredit
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.utils.html import format_html, format_html_join
from django.utils import timezone
from django.utils.dateparse import parse_date
from datetime import date, timedelta, datetime
from collections import defaultdict
import calendar
from django.http import HttpResponse, JsonResponse
from decimal import Decimal, InvalidOperation
from io import StringIO
import csv
from .models import ResidentRate, ReminderLog, Adjustment
import importlib
from django.urls import reverse
from urllib.parse import urlencode
from django.utils.text import slugify


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


def _is_website_admin(user):
    """Check if user is a website admin or superuser."""
    if getattr(user, 'is_superuser', False):
        return True
    return hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False)


def _has_payment_permission(user, permission_name, pg=None):
    """Check if user has a specific payment permission (can_delete_payments or can_edit_payments).
    
    Args:
        user: The user to check
        permission_name: 'can_delete_payments' or 'can_edit_payments'
        pg: Optional PG to check permission for
    
    Returns:
        bool: True if user has permission
    """
    # Website admins always have all permissions
    if _is_website_admin(user):
        return True
    
    # Check PG admin permissions
    try:
        from pgadmin.models import PGAdmin, PGAdminPermission
        pg_admins = PGAdmin.objects.filter(user=user).select_related('permissions')
        
        for pg_admin in pg_admins:
            # If specific PG requested, only check that PG's admin record
            if pg and pg_admin.pg_id != pg.id:
                continue
            
            try:
                if hasattr(pg_admin, 'permissions') and pg_admin.permissions:
                    if getattr(pg_admin.permissions, permission_name, False):
                        return True
            except PGAdminPermission.DoesNotExist:
                continue
    except Exception:
        pass
    
    return False


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

        # Attach room_no to each payment by finding the user's booking in this PG.
        # Prefer a booking whose effective period includes the payment.date. Fall back to latest booking.
        try:
            user_ids = list(items.values_list('user_id', flat=True).distinct())
            if user_ids:
                from bookings.models import Booking
                from django.core.cache import cache
                import hashlib
                # Build a cache key based on pg id and user ids (stable ordering)
                user_ids_sorted = sorted(set(user_ids))
                ids_key = ','.join(map(str, user_ids_sorted))
                cache_key = f"payments_bookings_pg{pg.id}_users_{hashlib.md5(ids_key.encode()).hexdigest()}"

                bookings_by_user = cache.get(cache_key)
                if bookings_by_user is None:
                    # Retrieve bookings for these users in one query, most recent first per user
                    bookings_qs = Booking.objects.filter(
                        pg=pg, user_id__in=user_ids_sorted, status__in=[Booking.APPROVED, Booking.COMPLETED]
                    ).select_related('room').order_by('user_id', '-id')
                    from collections import defaultdict
                    bookings_by_user = defaultdict(list)
                    for b in bookings_qs:
                        bookings_by_user[b.user_id].append(b)
                    # Cache the mapping for a short period to reduce DB hits on large result sets
                    cache.set(cache_key, bookings_by_user, 60)  # 60 seconds

                # Convert payments to list to allow attribute attachment
                items = list(items)
                from datetime import date as _date
                for p in items:
                    assigned = None
                    candidates = bookings_by_user.get(getattr(p, 'user_id', None), [])
                    payment_date = getattr(p, 'date', None)
                    # Try to find a booking whose range covers payment_date
                    if payment_date and candidates:
                        for b in candidates:
                            # determine effective start and end for booking
                            start = getattr(b, 'joining_date', None) or getattr(b, 'start_date', None) or getattr(b, 'payment_date', None)
                            end = getattr(b, 'leaving_date', None)
                            if start and (end is None or payment_date <= end) and payment_date >= start:
                                if getattr(b, 'room', None):
                                    assigned = getattr(b.room, 'room_no', None)
                                    break
                    # Fallback: use the most recent booking (first in candidates due to ordering)
                    if not assigned and candidates:
                        b = candidates[0]
                        if getattr(b, 'room', None):
                            assigned = getattr(b.room, 'room_no', None)

                    if assigned:
                        setattr(p, 'room_no', assigned)
                    else:
                        if not hasattr(p, 'room_no'):
                            setattr(p, 'room_no', None)
        except Exception:
            # Non-fatal: if anything goes wrong, continue without room enrichment
            pass

    # Prepare filters context
    filters = {
        'q': q,
        'ym': ym,
        'date_from': request.GET.get('date_from') or '',
        'date_to': request.GET.get('date_to') or '',
        'year': year or '',
        'month': month or '',
    }
    
    # Check permissions for edit/delete buttons
    can_edit_payments = _has_payment_permission(request.user, 'can_edit_payments', pg)
    can_delete_payments = _has_payment_permission(request.user, 'can_delete_payments', pg)
    
    return render(request, 'finance/payments_list.html', {
        "pg": pg, 
        "items": items, 
        "pgs": list(_admin_pgs(request.user)), 
        "filters": filters,
        "can_edit_payments": can_edit_payments,
        "can_delete_payments": can_delete_payments,
    })


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
    
    # Check edit permission for existing payments (new payments don't need special permission)
    if pk and not _has_payment_permission(request.user, 'can_edit_payments', pg):
        messages.error(request, 'You do not have permission to edit payments.')
        return redirect('payments_list')
    
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
            if obj.status == 'success' and (prev_status != 'success'):
                try:
                    from finance.signals import deliver_payment_receipt
                    deliver_payment_receipt(obj)
                except Exception as e:
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
    - Restricted to users with can_delete_payments permission.
    - After deletion, redirect back to payments list for that PG.
    """
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    
    # Fetch payment first to check permission for specific PG
    payment = get_object_or_404(Payment, pk=pk)
    
    # Check delete permission (website admins or users with can_delete_payments)
    if not _has_payment_permission(request.user, 'can_delete_payments', payment.pg):
        messages.error(request, 'You do not have permission to delete payments.')
        return redirect('payments_list')
    
    # Only allow POST to delete
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('payments_list')
    # Determine active PG for context and authorization
    pg = _active_pg(request)
    # Ensure payment belongs to an authorized PG
    if not _is_authorized_pg(request.user, payment.pg_id):
        messages.error(request, "You do not have access to this PG.")
        return redirect('payments_list')
    # Delete and log
    pid = payment.id
    payment.delete()
    log(request.user, 'payment_deleted', 'Payment', pid)
    messages.success(request, "Payment deleted.")
    return redirect('payments_list')


@login_required
def my_payments(request):
    """Show all payments made by the logged-in resident with receipt-style details."""
    # Support sorting by payment date (oldest/newest)
    sort_param = (request.GET.get('sort') or '').strip().lower()
    if sort_param == 'oldest':
        ordering = ('date', 'id')
    else:
        ordering = ('-date', '-id')

    base_qs = (
        Payment.objects.filter(user=request.user)
        .select_related('pg')
        .order_by(*ordering)
    )
    success_total = base_qs.filter(status='success').aggregate(total=Sum('amount')).get('total')
    success_total = success_total or Decimal('0')
    status_counts_qs = base_qs.values('status').annotate(count=Count('id'))
    status_counts = {item['status']: item['count'] for item in status_counts_qs}
    success_count = status_counts.get('success', 0)
    pending_failed_count = status_counts.get('pending', 0) + status_counts.get('failed', 0)
    payments_list = list(base_qs)

    from finance.signals import _build_receipt_context

    payment_rows = []
    for idx, payment in enumerate(payments_list):
        receipt_ctx = _build_receipt_context(payment)
        payment_rows.append({
            'payment': payment,
            'receipt': receipt_ctx,
            'collapse_id': f"payment-{payment.id}",
            'is_first': idx == 0,
            'status_badge': {
                'success': 'bg-success',
                'pending': 'bg-warning text-dark',
                'failed': 'bg-danger',
            }.get(payment.status, 'bg-secondary'),
            'status_label': payment.get_status_display(),
            'mode_label': payment.get_mode_display(),
            'type_label': payment.get_type_display(),
        })

    from django.urls import reverse

    context = {
        'payment_rows': payment_rows,
        'total_success': success_total,
        'total_count': len(payment_rows),
        'status_counts': status_counts,
        'success_count': success_count,
        'pending_failed_count': pending_failed_count,
        'current_sort': 'oldest' if sort_param == 'oldest' else 'newest',
        'sort_url_oldest': f"{reverse('my_payments')}?sort=oldest",
        'sort_url_newest': f"{reverse('my_payments')}?sort=newest",
    }
    return render(request, 'finance/my_payments.html', context)
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
    # support multi-select categories (category may appear multiple times)
    category_list = request.GET.getlist('category')
    # For backward compatibility, accept single comma-separated value
    if not category_list and request.GET.get('category'):
        raw_cat = (request.GET.get('category') or '').strip()
        if raw_cat:
            category_list = [s.strip() for s in raw_cat.split(',') if s.strip()]
    date_from = parse_date((request.GET.get('date_from') or '').strip())
    date_to = parse_date((request.GET.get('date_to') or '').strip())
    if pg:
        items = Expenditure.objects.filter(pg=pg)
        if category_list:
            # Convert to integers when possible and filter by custom category ids
            cat_ids = []
            legacy_vals = []
            for c in category_list:
                try:
                    cat_ids.append(int(c))
                except (ValueError, TypeError):
                    legacy_vals.append(c)
            if cat_ids:
                items = items.filter(category_custom_id__in=cat_ids)
            if legacy_vals:
                items = items.filter(category__in=legacy_vals)
        if date_from:
            items = items.filter(date__gte=date_from)
        if date_to:
            items = items.filter(date__lte=date_to)
        if q:
            # Search in notes, legacy category, and custom category name
            items = items.filter(
                Q(notes__icontains=q) | 
                Q(category__icontains=q) |
                Q(category_custom__name__icontains=q)
            )
        items = items.select_related('category_custom').order_by('-date', '-id')
        total = items.aggregate(total=Sum('amount')).get('total') or 0
        # Pagination
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        page_size = 10
        try:
            page_size = int(request.GET.get('per_page') or 10)
        except (ValueError, TypeError):
            page_size = 10
        paginator = Paginator(items, page_size)
        page_number = request.GET.get('page')
        try:
            page_obj = paginator.get_page(page_number)
        except Exception:
            page_obj = paginator.get_page(1)
        items = page_obj
    filters = {
        'q': q,
        'category': ','.join(category_list) if category_list else '',
        'date_from': request.GET.get('date_from') or '',
        'date_to': request.GET.get('date_to') or '',
        'page_size': page_size,
    }
    # Get categories for the export modal dropdown
    # Also pass legacy/default category choices
    legacy_categories = Expenditure.CATEGORY_CHOICES
    # Get legacy category names to exclude from custom categories
    legacy_category_names = [label for value, label in legacy_categories]
    # Filter out custom categories that have the same name as legacy categories
    categories = ExpenditureCategory.objects.filter(pg=pg).exclude(
        name__in=legacy_category_names
    ).order_by('name') if pg else []
    
    return render(request, 'finance/expenditure_list.html', {
        "pg": pg, 
        "items": items, 
        "pgs": list(_admin_pgs(request.user)), 
        "filters": filters, 
        "total": total, 
        "paginator": getattr(items, 'paginator', None), 
        "page_obj": getattr(items, 'page_obj', None),
        "categories": categories,
        "legacy_categories": legacy_categories
    })


@login_required
def expenditure_list_json(request):
    """Return expenditures for the active PG as JSON for client-side filtering/sorting.
    This endpoint returns a list of items with the minimal fields required by the UI.
    """
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'PG Admin access required.'}, status=403)
    req_pg = request.GET.get('pg')
    if req_pg and not _is_authorized_pg(request.user, req_pg):
        return JsonResponse({'error': 'Unauthorized PG.'}, status=403)
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'items': [], 'total': 0})

    qs = Expenditure.objects.filter(pg=pg).select_related('category_custom').order_by('-date', '-id')
    # Limit to a reasonable count to avoid huge payloads; increase if needed.
    MAX_ITEMS = 5000
    qs = qs[:MAX_ITEMS]

    items = []
    for e in qs:
        items.append({
            'id': e.id,
            'date': e.date.isoformat(),
            'category': e.get_category_display(),
            'category_id': e.category_custom_id,
            'amount': float(e.amount),
            'notes': e.notes or '',
        })

    total = qs.aggregate(total=Sum('amount')).get('total') or 0
    return JsonResponse({'items': items, 'total': float(total)})


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
        form = ExpenditureForm(request.POST, instance=instance, pg=pg)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.pg = pg
            obj.save()
            log(request.user, 'expenditure_saved', 'Expenditure', obj.id)
            messages.success(request, "Expenditure saved.")
            return redirect('expenditure_list')
    else:
        form = ExpenditureForm(instance=instance, pg=pg)
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


def _get_payment_cycle_for_month(booking, m_first: date) -> tuple[date, date, date | None, date | None, int, int]:
    """Return payment-cycle details for the calendar month that starts at ``m_first``.

    Returns a tuple containing:
        cycle_start: datetime.date - nominal cycle start for this month
        cycle_end: datetime.date - nominal cycle end (payment day - 1 of next month)
        effective_start: datetime.date | None - actual start after joining/availability
        effective_end: datetime.date | None - actual end before leaving (if any)
        effective_days: int - number of chargeable days within this cycle (>= 0)
        cycle_days: int - total days in the nominal cycle (>= 0)

    The calculation follows the requested scenarios:
    - Scenario 1: first month with mid-cycle joining → start at joining date, end at payment-1 next month
    - Scenario 2: regular cycle → payment date to payment date-1 next month
    - Scenario 3: leaving mid-cycle → payment date to leaving date
    """

    payment_anchor = _payment_anchor_for_booking(booking)
    joining_date = booking.joining_date or booking.start_date or (booking.created_at.date() if getattr(booking, 'created_at', None) else None)
    leaving_date = booking.leaving_date

    if not payment_anchor and not joining_date:
        # Fallback: treat as month starting on m_first
        payment_day = 1
    else:
        payment_day = (payment_anchor or joining_date).day if (payment_anchor or joining_date) else 1

    # Nominal cycle start for this month:
    days_in_month = calendar.monthrange(m_first.year, m_first.month)[1]
    cycle_start_day = min(payment_day, days_in_month)
    cycle_start = date(m_first.year, m_first.month, cycle_start_day)

    # Next month calculation
    if m_first.month == 12:
        next_month = 1
        next_year = m_first.year + 1
    else:
        next_month = m_first.month + 1
        next_year = m_first.year
    days_in_next_month = calendar.monthrange(next_year, next_month)[1]
    next_cycle_day = min(payment_day, days_in_next_month)
    next_cycle_date = date(next_year, next_month, next_cycle_day)
    cycle_end = next_cycle_date - timedelta(days=1)

    # Full cycle days (for pro-rating denominator)
    cycle_days = max(0, (cycle_end - cycle_start).days + 1)

    # Effective range considering joining/leaving
    effective_start = cycle_start
    if joining_date and joining_date > effective_start:
        effective_start = joining_date

    effective_end = cycle_end
    if leaving_date:
        # When there's a leaving date, calculate until leaving_date - 1
        adjusted_leaving = leaving_date - timedelta(days=1)
        if adjusted_leaving < effective_end:
            effective_end = adjusted_leaving

    if effective_end < cycle_start or (joining_date and joining_date > cycle_end):
        # No overlap with this cycle
        return cycle_start, cycle_end, None, None, 0, cycle_days

    if effective_start < cycle_start:
        effective_start = cycle_start

    if effective_end > cycle_end:
        effective_end = cycle_end

    effective_days = max(0, (effective_end - effective_start).days + 1)

    return cycle_start, cycle_end, effective_start, effective_end, effective_days, cycle_days


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


def _room_share_label(room) -> str:
    """Return a human-friendly share label for the room, e.g. '3-Sharing'."""
    if not room:
        return ''
    share_val = getattr(room, 'total_shares', '')
    if share_val in (None, ''):
        return ''
    try:
        share_str = str(int(share_val))
    except Exception:
        share_str = str(share_val)
    share_map = dict(Fees.SHARE_TYPES)
    if share_str in share_map:
        return share_map[share_str]
    # Fallback when share isn't one of the predefined choices
    return f"{share_val}-Sharing"


def _billing_period_from_payment_date(payment_date: date) -> tuple[date | None, date | None]:
    """Given a payment date, return the inferred billing period (from, to).

    From date defaults to the payment date. To date is computed as the same-day
    in the next month minus one day (e.g., 23 Oct → 22 Nov).
    """
    if not payment_date:
        return None, None
    next_month = 1 if payment_date.month == 12 else payment_date.month + 1
    next_year = payment_date.year + (1 if payment_date.month == 12 else 0)
    last_day_next_month = calendar.monthrange(next_year, next_month)[1]
    target_day = min(payment_date.day, last_day_next_month)
    next_cycle_marker = date(next_year, next_month, target_day)
    to_date = next_cycle_marker - timedelta(days=1)
    return payment_date, to_date


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
        # Use tolerance of 1 rupee to handle accumulated rounding errors
        # from multiple pro-rated calculations
        tolerance = 1.0
        if collected >= expected - tolerance:
            return 'paid', 'Paid', 'status-paid'
        if collected > 0:
            return 'partial', 'Partial', 'status-partial'
        return 'unpaid', 'Unpaid', 'status-unpaid'

    return 'upcoming', 'No Due', 'status-upcoming'


def _expected_rent_for_user_pg_month(u, pg, booking, m_first, m_last, today=None, details=None) -> float:
    from datetime import datetime
    from bookings.models import RoomSwap

    def _add_detail(detail_type: str, **kwargs) -> None:
        if details is None:
            return
        payload = {'type': detail_type}
        payload.update(kwargs)
        details.append(payload)

    # Day-wise booking: compute expected directly based on configured fee.
    if hasattr(booking, 'booking_type') and booking.booking_type == 'daywise':
        daywise_fee = float(getattr(pg, 'daywise_fee', 0) or 0)
        if daywise_fee <= 0:
            return 0.0

        start = booking.joining_date or booking.start_date or booking.created_at.date()
        end = booking.leaving_date or m_last
        actual_start = max(start, m_first)
        actual_end = min(end, m_last)
        if actual_start > actual_end:
            return 0.0

        hours = None
        if booking.start_time and booking.end_time:
            start_dt = datetime.combine(actual_start, booking.start_time)
            end_dt = datetime.combine(actual_end, booking.end_time)
            duration = end_dt - start_dt
            hours = max(0.0, duration.total_seconds() / 3600)
            if hours >= 12:
                days = int(hours / 24) + (1 if hours % 24 >= 12 else 0)
            else:
                days = 0
        else:
            days = (actual_end - actual_start).days + 1

        expected = round(daywise_fee * max(0, days), 2)
        note_bits = [f"Day-wise fee ₹{daywise_fee:.2f}/day"]
        if hours is not None:
            note_bits.append(f"Duration ≈ {hours:.1f} hours")
        _add_detail(
            'daywise_segment',
            room_no=getattr(getattr(booking, 'room', None), 'room_no', '—'),
            share_label=_room_share_label(getattr(booking, 'room', None)),
            start=actual_start,
            end=actual_end,
            days=max(0, days),
            rate=daywise_fee,
            rate_unit='day',
            amount=expected,
            source='Day-wise booking',
            notes='; '.join(note_bits),
        )
        # Day-wise bookings are typically immediate, so skip month gating logic.
        return expected

    # Use payment cycle calculation instead of calendar month overlap
    cycle_start, cycle_end, effective_start, effective_end, effective_days, cycle_days = _get_payment_cycle_for_month(booking, m_first)

    # Fetch swaps that occur within the billing cycle (cycle_start..cycle_end).
    # Previously we fetched swaps based on the calendar month (m_first..m_last)
    # which missed swaps that happened in the cycle but fell in the following
    # calendar month. Querying by cycle bounds ensures segments are split
    # correctly across the entire billing cycle.
    swaps = RoomSwap.objects.filter(
        booking=booking,
        status=RoomSwap.COMPLETED,
        effective_date__gte=cycle_start,
        effective_date__lte=cycle_end,
    ).order_by('effective_date')

    if effective_days <= 0 or cycle_days <= 0 or effective_start is None or effective_end is None:
        return 0.0

    resident_rate = ResidentRate.objects.filter(user=u, pg=pg, active=True).first()
    custom_monthly = float(resident_rate.amount) if resident_rate else None
    custom_source = 'Custom rate' if resident_rate else ''

    if details is not None:
        for swap in swaps:
            _add_detail(
                'swap_event',
                effective_date=swap.effective_date,
                from_room_no=getattr(getattr(swap, 'from_room', None), 'room_no', '—'),
                from_share_label=_room_share_label(getattr(swap, 'from_room', None)),
                to_room_no=getattr(getattr(swap, 'to_room', None), 'room_no', '—'),
                to_share_label=_room_share_label(getattr(swap, 'to_room', None)),
            )

    if not swaps.exists():
        room = getattr(booking, 'room', None)
        share_label = _room_share_label(room)
        if custom_monthly is not None:
            monthly = custom_monthly
            source = custom_source
        else:
            share_type = str(getattr(room, 'total_shares', '') or '')
            fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
            monthly = float(getattr(fees, 'monthly_fee', 0) or 0)
            source = f"{share_label} fee" if share_label else 'Default fee'

        if monthly <= 0:
            return 0.0

        # Pro-rate based on payment cycle length (Scenario 1/2/3)
        expected = round((monthly * effective_days) / cycle_days, 2)
        _add_detail(
            'segment',
            room_no=getattr(room, 'room_no', '—'),
            share_label=share_label,
            start=effective_start,
            end=effective_end,
            days=effective_days,
            rate=monthly,
            rate_unit='month',
            amount=expected,
            source=source,
        )
    else:
        # Handle room swaps within the month
        # For swaps, we still need to calculate based on the payment cycle
        segments = []
        
        # Use cycle_start and cycle_end from payment cycle calculation
        period_start = effective_start
        period_end = effective_end
        current_start = period_start
        current_room = None

        prior_swaps = RoomSwap.objects.filter(
            booking=booking,
            status=RoomSwap.COMPLETED,
            effective_date__lt=period_start,
        ).order_by('-effective_date')

        if prior_swaps.exists():
            current_room = prior_swaps.first().to_room or booking.room
        else:
            first_swap_in_month = swaps.first()
            current_room = (first_swap_in_month.from_room if first_swap_in_month and getattr(first_swap_in_month, 'from_room', None) else booking.room)

        for swap in swaps:
            swap_date = swap.effective_date
            # normalize datetimes to date objects if necessary
            if hasattr(swap_date, 'date'):
                try:
                    swap_date = swap_date.date()
                except Exception:
                    pass
            if current_start < swap_date:
                segments.append({'start': current_start, 'end': swap_date - timedelta(days=1), 'room': current_room})
            current_room = swap.to_room or booking.room
            current_start = swap_date

        if current_start <= period_end:
            segments.append({'start': current_start, 'end': period_end, 'room': current_room})

        expected = 0.0
        for segment in segments:
            seg_start = segment['start']
            seg_end = segment['end']
            seg_room = segment['room']
            seg_days = (seg_end - seg_start).days + 1

            if custom_monthly is not None:
                monthly = custom_monthly
                source = custom_source or 'Custom rate'
            else:
                share_type = str(getattr(seg_room, 'total_shares', '') or '')
                fees = Fees.objects.filter(pg=pg, share_type=share_type).first()
                monthly = float(getattr(fees, 'monthly_fee', 0) or 0)
                share_label = _room_share_label(seg_room)
                source = f"{share_label} fee" if share_label else 'Default fee'

            if monthly <= 0:
                continue

            # Pro-rate based on FULL payment cycle days
            seg_expected = round((monthly * seg_days) / cycle_days, 2)
            expected += seg_expected
            _add_detail(
                'segment',
                room_no=getattr(seg_room, 'room_no', '—'),
                share_label=_room_share_label(seg_room),
                start=seg_start,
                end=seg_end,
                days=seg_days,
                rate=monthly,
                rate_unit='month',
                amount=seg_expected,
                source=source,
            )

        expected = round(expected, 2)

    if expected <= 0:
        return 0.0

    if today is None:
        today = timezone.now().date()

    payment_anchor = _payment_anchor_for_booking(booking)

    month_marker = (m_first.year, m_first.month)
    today_marker = (today.year, today.month)

    # For months in the future, compute and return the expected amount so the
    # UI can display it. Do NOT gate the expected value here — the status
    # determination (e.g. showing "No Due" until the payment date arrives)
    # is handled by the status/resolution logic elsewhere.
    # (Previously we returned 0.0 here which hid expected amounts for future months.)

    if payment_anchor:
        # Calculate due date based on payment_anchor day
        days_in_current_month = calendar.monthrange(m_first.year, m_first.month)[1]
        due_day = min(payment_anchor.day, days_in_current_month)
        due_date = date(m_first.year, m_first.month, due_day)
        if month_marker == today_marker and today < due_date:
            return 0.0

    # Apply monthly adjustments (discounts/increments)
    year_month_str = m_first.strftime('%Y-%m')
    adjustments = MonthlyAdjustment.objects.filter(
        user=u,
        pg=pg,
        is_active=True
    )
    
    for adj in adjustments:
        if adj.applies_to_month(year_month_str):
            if adj.adjustment_type == 'discount':
                discount_amount = float(adj.amount)
                expected = max(0, expected - discount_amount)
                _add_detail(
                    'adjustment',
                    adjustment_type='Discount',
                    amount=-discount_amount,
                    notes=f"Discount: {adj.notes}" if adj.notes else "Discount applied",
                )
            else:  # increment
                increment_amount = float(adj.amount)
                expected = expected + increment_amount
                _add_detail(
                    'adjustment',
                    adjustment_type='Increment',
                    amount=increment_amount,
                    notes=f"Increment: {adj.notes}" if adj.notes else "Increment applied",
                )
    
    expected = round(expected, 2)

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
    category_values = request.GET.getlist('category')
    if not category_values and request.GET.get('category'):
        raw_cat = (request.GET.get('category') or '').strip()
        if raw_cat:
            category_values = [s.strip() for s in raw_cat.split(',') if s.strip()]
    cat_ids = []
    cat_id_seen = set()
    legacy_vals = []
    legacy_seen = set()
    include_uncat = False
    for val in category_values:
        if val is None:
            continue
        sval = str(val).strip()
        if not sval:
            continue
        if sval == '__uncat':
            include_uncat = True
            continue
        try:
            cid = int(sval)
        except (ValueError, TypeError):
            if sval not in legacy_seen:
                legacy_vals.append(sval)
                legacy_seen.add(sval)
        else:
            if cid not in cat_id_seen:
                cat_ids.append(cid)
                cat_id_seen.add(cid)
    date_from = parse_date((request.GET.get('date_from') or '').strip())
    date_to = parse_date((request.GET.get('date_to') or '').strip())
    items = Expenditure.objects.none()
    if pg:
        items = Expenditure.objects.filter(pg=pg)
        if cat_ids or legacy_vals or include_uncat:
            cat_filter = Q()
            if cat_ids:
                cat_filter |= Q(category_custom_id__in=cat_ids)
            if legacy_vals:
                cat_filter |= Q(category__in=legacy_vals)
            if include_uncat:
                cat_filter |= (
                    Q(category_custom__isnull=True)
                    & (
                        Q(category__isnull=True)
                        | Q(category__exact='')
                        | Q(category__iexact='uncategorized')
                    )
                )
            if cat_filter:
                items = items.filter(cat_filter)
        # Handle date range: if only date_from is provided, export all from that date onwards
        if date_from and date_to:
            items = items.filter(date__gte=date_from, date__lte=date_to)
        elif date_from:
            # Only from_date provided: export all expenses from that date onwards
            items = items.filter(date__gte=date_from)
        elif date_to:
            # Only to_date provided: export all expenses up to that date
            items = items.filter(date__lte=date_to)
        items = items.order_by('date', 'id')  # chronological for export

    id_name_map = {}
    if pg and cat_ids:
        id_name_map = dict(ExpenditureCategory.objects.filter(pg=pg, id__in=cat_ids).values_list('id', 'name'))
    
    # Build a map of legacy category values to display names
    legacy_name_map = dict(Expenditure.CATEGORY_CHOICES)
    
    selected_category_labels = []
    for cid in cat_ids:
        selected_category_labels.append(id_name_map.get(cid, f"Category #{cid}"))
    # Convert legacy values to their display names
    for legacy_val in legacy_vals:
        display_name = legacy_name_map.get(legacy_val, legacy_val)
        selected_category_labels.append(display_name)
    if include_uncat:
        selected_category_labels.append('Uncategorized')

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
    if selected_category_labels:
        fil_parts.append(f"Categories: {', '.join(selected_category_labels)}")
    if date_from and date_to:
        fil_parts.append(f"Date Range: {date_from:%Y-%m-%d} to {date_to:%Y-%m-%d}")
    elif date_from:
        fil_parts.append(f"From: {date_from:%Y-%m-%d} onwards")
    elif date_to:
        fil_parts.append(f"Up to: {date_to:%Y-%m-%d}")
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


# =============== Expenditure Category Management ===============
@login_required
def expenditure_categories_list(request):
	"""API endpoint to list all categories for a PG (returns JSON)."""
	if not _require_pg_admin(request.user):
		return JsonResponse({'error': 'PG Admin access required.'}, status=403)
	
	req_pg = request.GET.get('pg')
	if req_pg and not _is_authorized_pg(request.user, req_pg):
		return JsonResponse({'error': 'Unauthorized PG.'}, status=403)
	
	pg = _active_pg(request)
	if not pg:
		return JsonResponse({'error': 'No PG selected.'}, status=400)
	
	categories = ExpenditureCategory.objects.filter(pg=pg).order_by('display_order', 'name')
	data = [{
		'id': cat.id,
		'name': cat.name,
		'slug': cat.slug,
		'is_default': cat.is_default,
		'display_order': cat.display_order,
	} for cat in categories]
	
	return JsonResponse({'categories': data})


@login_required
@transaction.atomic
def expenditure_category_create(request):
	"""API endpoint to create a new category (POST JSON)."""
	if not _require_pg_admin(request.user):
		return JsonResponse({'error': 'PG Admin access required.'}, status=403)
	
	if request.method != 'POST':
		return JsonResponse({'error': 'POST required.'}, status=405)
	
	req_pg = request.POST.get('pg')
	if req_pg and not _is_authorized_pg(request.user, req_pg):
		return JsonResponse({'error': 'Unauthorized PG.'}, status=403)
	
	pg = _active_pg(request)
	if not pg:
		return JsonResponse({'error': 'No PG selected.'}, status=400)
	
	name = (request.POST.get('name') or '').strip()
	if not name:
		return JsonResponse({'error': 'Category name is required.'}, status=400)
	
	# Generate slug from name
	base_slug = slugify(name)
	slug = base_slug
	counter = 1
	while ExpenditureCategory.objects.filter(pg=pg, slug=slug).exists():
		slug = f"{base_slug}-{counter}"
		counter += 1
	
	# Get max display_order
	max_order = ExpenditureCategory.objects.filter(pg=pg).aggregate(
		max_order=Count('id')
	).get('max_order') or 0
	
	category = ExpenditureCategory.objects.create(
		pg=pg,
		name=name,
		slug=slug,
		is_default=False,
		display_order=max_order + 1
	)
	
	log(request.user, 'create', 'ExpenditureCategory', category.id, f'Created expenditure category: {name}')
	
	return JsonResponse({
		'success': True,
		'category': {
			'id': category.id,
			'name': category.name,
			'slug': category.slug,
			'is_default': category.is_default,
			'display_order': category.display_order,
		}
	})


@login_required
@transaction.atomic
def expenditure_category_delete(request, pk):
	"""API endpoint to delete a category (POST)."""
	if not _require_pg_admin(request.user):
		return JsonResponse({'error': 'PG Admin access required.'}, status=403)
	
	if request.method != 'POST':
		return JsonResponse({'error': 'POST required.'}, status=405)
	
	category = get_object_or_404(ExpenditureCategory, pk=pk)
	
	# Check authorization
	if not _is_authorized_pg(request.user, category.pg.id):
		return JsonResponse({'error': 'Unauthorized PG.'}, status=403)
	
	# Prevent deletion of default categories
	if category.is_default:
		return JsonResponse({'error': 'Cannot delete default categories.'}, status=400)
	
	# Note: We use SET_NULL on the foreign key, so deleting a category
	# will set category_custom to NULL on related expenditures (they won't be deleted)
	
	name = category.name
	category_id = category.id
	category.delete()
	
	log(request.user, 'delete', 'ExpenditureCategory', category_id, f'Deleted expenditure category: {name}')
	
	return JsonResponse({'success': True})


def _collected_for_user_pg_month(u, pg, m_first, m_last) -> float:
    # Count rent/fee payments for the selected month
    # Prefer the payment.from_date (canonical billing month) when set so that
    # payments made earlier but intended for the next billing month are
    # attributed to that month. Fall back to payment.date only when
    # from_date is null to avoid double-counting the same payment.
    # Include both monthly 'fee' payments and 'daywise' payments when
    # computing collected amounts for a billing month so day-wise
    # bookings are attributed correctly.
    p_qs = Payment.objects.filter(user=u, pg=pg, status='success', type__in=['fee', 'daywise']).filter(
        Q(from_date__gte=m_first, from_date__lte=m_last) | Q(from_date__isnull=True, date__gte=m_first, date__lte=m_last)
    )
    p_sum = p_qs.aggregate(total=Sum('amount')).get('total') or 0
    
    # Also include credit adjustments as collected (waivers/discounts reduce what's owed)
    # Note: deposit_deduction is NOT included as it's a separate deduction from security deposit
    adj_sum = Adjustment.objects.filter(
        user=u, pg=pg, type='credit', 
        date__gte=m_first, date__lte=m_last
    ).aggregate(total=Sum('amount')).get('total') or 0
    
    return float(p_sum) + float(adj_sum)


def _advance_paid_for_user_pg(u, pg) -> float:
    adv = Payment.objects.filter(user=u, pg=pg, status='success', type='advance').aggregate(total=Sum('amount')).get('total') or 0
    return float(adv)


def _advance_paid_for_user_pg_month(u, pg, m_first: date, m_last: date) -> float:
    """Return total advance payments (successful) for the user in the given month range.

    This ensures advances are counted only in the month where the payment's transaction
    date lies (date between m_first and m_last inclusive).
    """
    adv = Payment.objects.filter(
        user=u, pg=pg, status='success', type='advance', date__gte=m_first, date__lte=m_last
    ).aggregate(total=Sum('amount')).get('total') or 0
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

    user_ids = list(by_user.keys())
    month_start = m_first
    credits_by_referrer: dict[int, dict[str, list[ReferralCredit]]] = defaultdict(lambda: {'redeemed': [], 'pending': [], 'scheduled': [], 'history': []})
    if user_ids:
        credit_qs = (
            ReferralCredit.objects
            .filter(pg=pg, referrer_user_id__in=user_ids)
            .select_related('referrer_booking', 'referred_user')
        )
        for credit in credit_qs:
            entry = credits_by_referrer[credit.referrer_user_id]
            if credit.redeemed_for_month == month_start:
                entry['redeemed'].append(credit)
            elif credit.redeemed_on:
                entry['history'].append(credit)
            else:
                entry['pending'].append(credit)
                if credit.scheduled_month == month_start or credit.scheduled_month is None:
                    entry['scheduled'].append(credit)

    rows = []
    total_expected = 0.0
    total_collected = 0.0
    for user_id, bookings in by_user.items():
        # Sort segments by start date for deterministic output
        segs = []
        primary_seg = None
        row_segment_details = []
        row_swap_details = []
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
            detail_entries = []
            exp_part = _expected_rent_for_user_pg_month(b.user, pg, b, m_first, m_last, details=detail_entries)
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
            for entry in detail_entries:
                if entry.get('type') == 'swap_event':
                    row_swap_details.append(entry)
                else:
                    row_segment_details.append(entry)
        segs.sort(key=lambda x: (x['start'] or m_first, x['end'] or m_last))

        expected_total = round(sum((seg['expected'] or 0.0) for seg in segs), 2)
        # Referral credits applied/pending for the user this month
        credit_entry = credits_by_referrer.get(user_id)
        redeemed_total = 0.0
        if credit_entry:
            redeemed_total = round(sum(float((c.redeemed_amount or c.amount or 0)) for c in credit_entry['redeemed']), 2)
        expected_after_credit = max(0.0, round(expected_total - redeemed_total, 2))
        # Collected for the user across the month (unchanged)
        u = segs[0]['b'].user
        collected = _collected_for_user_pg_month(u, pg, m_first, m_last)
        pending = round(expected_after_credit - collected, 2)
        primary_due = primary_seg.get('payment_due') if primary_seg else None
        status, status_label, status_css = _resolve_status(expected_after_credit, float(collected), m_first, primary_due, today)
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
        if redeemed_total:
            exp_tip += f" Referral credit applied: -₹{redeemed_total:.2f}."
        scheduled_list = credit_entry['scheduled'] if credit_entry else []
        pending_current_total = 0.0
        if scheduled_list:
            pending_current_total = round(sum(float(c.amount or 0) for c in scheduled_list), 2)
            exp_tip += f" Pending referral credit to apply: -₹{pending_current_total:.2f}."
        referral_future = []
        if credit_entry:
            referral_future = [c for c in credit_entry['pending'] if c not in scheduled_list]

        # Build detailed expected breakdown for popover display
        month_label = f"{calendar.month_name[month]} {year}"
        sorted_segments = sorted(
            row_segment_details,
            key=lambda seg: ((seg.get('start') or m_first), (seg.get('end') or m_last)),
        )
        segment_lines = []
        for seg_detail in sorted_segments:
            seg_start = seg_detail.get('start') or m_first
            seg_end = seg_detail.get('end') or m_last
            start_label = seg_start.strftime('%d %b %Y')
            end_label = seg_end.strftime('%d %b %Y')
            meta_texts = []
            room_no = seg_detail.get('room_no')
            if room_no and room_no != '—':
                meta_texts.append(f"Room {room_no}")
            share_label = seg_detail.get('share_label')
            if share_label:
                meta_texts.append(share_label)
            meta_html = format_html(
                ' • {}',
                format_html_join(' • ', '{}', ((text,) for text in meta_texts)),
            ) if meta_texts else format_html('')
            day_count = int(seg_detail.get('days') or 0)
            day_label = format_html('{} day{}', day_count, '' if day_count == 1 else 's')
            rate_value = float(seg_detail.get('rate') or 0.0)
            rate_unit = 'day' if seg_detail.get('rate_unit') == 'day' else 'month'
            amount_value = float(seg_detail.get('amount') or 0.0)
            notes_items = []
            if seg_detail.get('source'):
                notes_items.append(str(seg_detail.get('source')))
            if seg_detail.get('notes'):
                notes_items.append(str(seg_detail.get('notes')))
            notes_html = format_html(
                '<br><span class="text-secondary small">{}</span>',
                ' • '.join(notes_items)
            ) if notes_items else format_html('')
            # Format numeric amounts into plain strings before passing to format_html
            rate_display = f"{rate_value:.2f}"
            amount_display = f"{amount_value:.2f}"
            segment_lines.append(
                format_html(
                    '<li><strong>{} → {}</strong>{} • {} × ₹{}/{} → <strong>₹{}</strong>{}</li>',
                    start_label,
                    end_label,
                    meta_html,
                    day_label,
                    rate_display,
                    rate_unit,
                    amount_display,
                    notes_html,
                )
            )
        segment_items = format_html_join('', '{}', ((line,) for line in segment_lines))
        segments_total_days = sum(int(entry.get('days') or 0) for entry in row_segment_details)
        segment_count = len(sorted_segments)

        swap_seen = {}
        for swap_detail in row_swap_details:
            key = (
                swap_detail.get('effective_date'),
                swap_detail.get('from_room_no'),
                swap_detail.get('from_share_label'),
                swap_detail.get('to_room_no'),
                swap_detail.get('to_share_label'),
            )
            if key not in swap_seen:
                swap_seen[key] = swap_detail
        unique_swaps = sorted(swap_seen.values(), key=lambda e: e.get('effective_date') or m_first)
        swap_lines = []
        for swap_entry in unique_swaps:
            effective_date = swap_entry.get('effective_date')
            eff_label = effective_date.strftime('%d %b %Y') if effective_date else '—'
            from_parts = []
            from_room = swap_entry.get('from_room_no')
            if from_room and from_room != '—':
                from_parts.append(f"Room {from_room}")
            from_share = swap_entry.get('from_share_label')
            if from_share:
                from_parts.append(from_share)
            from_label = ' '.join(from_parts) if from_parts else '—'
            to_parts = []
            to_room = swap_entry.get('to_room_no')
            if to_room and to_room != '—':
                to_parts.append(f"Room {to_room}")
            to_share = swap_entry.get('to_share_label')
            if to_share:
                to_parts.append(to_share)
            to_label = ' '.join(to_parts) if to_parts else '—'
            swap_lines.append(format_html('<li>{}: {} → {}</li>', eff_label, from_label, to_label))
        swap_items = format_html_join('', '{}', ((line,) for line in swap_lines))
        swap_count = len(unique_swaps)

        parts = [
            format_html('<div><strong>Gross expected:</strong> ₹{}</div>', f"{expected_total:.2f}"),
        ]
        if redeemed_total:
            parts.append(format_html('<div class="small text-success">Referral credit applied: -₹{}</div>', f"{redeemed_total:.2f}"))
        if pending_current_total:
            parts.append(format_html('<div class="small text-secondary">Pending referral credit: -₹{}</div>', f"{pending_current_total:.2f}"))
        parts.append(format_html('<div><strong>Net expected:</strong> ₹{}</div>', f"{expected_after_credit:.2f}"))
        pending_display = max(0.0, pending)
        parts.append(format_html('<div class="small">Collected: ₹{} • Pending: ₹{}</div>', f"{float(collected):.2f}", f"{pending_display:.2f}"))
        if segment_count:
            parts.append(
                format_html(
                    '<div class="small text-secondary">Total billed days: {} day{} in {}</div>',
                    segments_total_days,
                    '' if segments_total_days == 1 else 's',
                    month_label,
                )
            )
        if primary_seg and primary_seg.get('payment_due'):
            parts.append(
                format_html(
                    '<div class="small text-secondary">Payment due date: {}</div>',
                    primary_seg['payment_due'].strftime('%Y-%m-%d'),
                )
            )
        parts.append(format_html('<div class="fw-semibold mt-2">Stay segments ({})</div>', segment_count))
        if segment_lines:
            parts.append(format_html('<ul class="list-unstyled mb-1">{}</ul>', segment_items))
        else:
            parts.append(format_html('<p class="mb-0 small text-secondary">No stay segments recorded for this month.</p>'))
        parts.append(format_html('<div class="fw-semibold mt-2">Room swaps ({})</div>', swap_count))
        if swap_lines:
            parts.append(format_html('<ul class="list-unstyled mb-0">{}</ul>', swap_items))
        else:
            parts.append(format_html('<p class="mb-0 small text-secondary">No room swaps recorded in this period.</p>'))

        expected_breakdown_html = format_html_join('', '{}', ((p,) for p in parts))
        expected_breakdown_id = f"expected-breakdown-{u.id}"

        # Build advance payment details (all successful advances for this user in this PG)
        adv_qs = Payment.objects.filter(user=u, pg=pg, status='success', type='advance').order_by('date')
        adv_items = []
        adv_total_all = 0.0
        for adv in adv_qs:
            try:
                amt = float(adv.amount or 0.0)
            except Exception:
                amt = 0.0
            adv_total_all += amt
            adv_date = getattr(adv, 'date', None)
            date_label = adv_date.strftime('%d %b %Y') if adv_date else (getattr(adv, 'created_at', None) and adv.created_at.strftime('%d %b %Y')) or '—'
            # Gather meta: mode, reference/txn id, notes
            mode_val = (getattr(adv, 'mode', None) or '')
            txn_val = (getattr(adv, 'reference', None) or getattr(adv, 'txn_id', None) or '')
            notes_val = (getattr(adv, 'notes', None) or '')
            meta_parts = []
            if mode_val:
                meta_parts.append(str(mode_val))
            if txn_val:
                meta_parts.append(f"Txn: {txn_val}")
            if notes_val:
                # Truncate long notes for popover preview
                meta_parts.append(str(notes_val)[:160])
            meta_html = ' • '.join(meta_parts)
            amount_display = f"{amt:.2f}"
            # Link to payment edit/view if URL name exists
            try:
                pay_url = reverse('payments_edit', args=[adv.id])
                link_html = format_html(' <a href="{}" target="_blank" class="ms-2">View</a>', pay_url)
            except Exception:
                link_html = format_html('')
            if meta_html:
                adv_items.append(format_html('<li><strong>{}</strong>: ₹{} <span class="small text-secondary">{}</span> {}</li>', date_label, amount_display, meta_html, link_html))
            else:
                adv_items.append(format_html('<li><strong>{}</strong>: ₹{} {}</li>', date_label, amount_display, link_html))
        advance_breakdown_html = format_html_join('', '{}', ((line,) for line in adv_items)) if adv_items else format_html('')
        advance_breakdown_id = f"advance-breakdown-{u.id}"

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
        # Exclude tenants who left on or before the payment due date for this month.
        # Example: payment due 10th, leaving on 9th or 10th => do not show in overview/export for this month.
        if latest_end and payment_due and payment_due >= latest_end:
            continue
        rows.append({
            'user': u,
            'room_no': getattr(last_seg['b'].room, 'room_no', '—'),
            'expected': expected_after_credit,
            'expected_tip': exp_tip,
            'collected': round(float(collected), 2),
            'pending': max(0.0, pending),
            'status': status,
            'status_label': status_label,
            'status_css': status_css,
            'joining': earliest_start,
            'leaving': latest_end,
            'whatsapp_phone': digits,
            # Advance collected in THIS month only (transaction date within m_first..m_last)
            'advance': round(_advance_paid_for_user_pg_month(u, pg, m_first, m_last), 2),
            # Advance total (all successful advance payments for this user in this PG)
            'advance_total': round(adv_total_all, 2),
            'advance_details_html': advance_breakdown_html,
            'advance_breakdown_id': advance_breakdown_id,
            'payment_due_date': payment_due,
            'payment_anchor': payment_anchor,
            'payment_due_day': payment_due_day,
            'payment_anchor_iso': payment_anchor.isoformat() if payment_anchor else '',
            'payment_date_iso': payment_due.isoformat() if payment_due else '',
            'primary_booking_id': primary_booking_id,
            'referral_adjustment': redeemed_total,
            'referral_pending_total': pending_current_total,
            'referral_pending': scheduled_list,
            'referral_redeemed': credit_entry['redeemed'] if credit_entry else [],
            'referral_future': referral_future,
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
            'expected_breakdown_html': expected_breakdown_html,
            'expected_breakdown_id': expected_breakdown_id,
            # Flag if any of the stay segments are day-wise bookings. Templates
            # can use this to show a 'Day-wise' indicator beside the resident name.
            'is_daywise': any(getattr(seg.get('b'), 'booking_type', None) == 'daywise' for seg in segs),
        })
        total_expected += expected_after_credit
        total_collected += float(collected)

    # Filters
    # Compute total advance across all rows before applying status filter
    total_advance_all = round(sum((r.get('advance') or 0.0) for r in rows), 2)
    only = request.GET.get('only')
    if only in ('paid', 'unpaid', 'partial', 'upcoming'):
        rows = [r for r in rows if r['status'] == only]
    
    # Old Month Dues filter: show tenants who had dues remaining from the previous month
    elif only == 'old_dues':
        # Calculate previous month range
        pm_first, pm_last, pm_days = _month_range(prev_year, prev_month)
        old_dues_user_ids = set()
        
        for row in rows:
            u = row['user']
            # Get previous month's expected rent for this user
            pm_expected = 0.0
            pm_collected = 0.0
            
            # Find bookings that overlapped with previous month
            prev_bks = (
                Booking.objects.filter(
                    user=u,
                    status__in=[Booking.APPROVED, Booking.COMPLETED],
                    room__pg=pg,
                )
                .select_related('room')
            )
            
            for b in prev_bks:
                start = b.joining_date or b.start_date or b.created_at.date()
                end = b.leaving_date
                ov = _overlap_days(start, end, pm_first, pm_last)
                if ov > 0:
                    exp_part = _expected_rent_for_user_pg_month(u, pg, b, pm_first, pm_last)
                    pm_expected += exp_part
            
            # Get collected for previous month
            pm_collected = _collected_for_user_pg_month(u, pg, pm_first, pm_last)
            
            # Calculate pending dues from previous month
            pm_pending = round(pm_expected - pm_collected, 2)
            
            # Add to old dues list if they had pending dues > 0
            if pm_pending > 0:
                old_dues_user_ids.add(u.id)
                # Store previous month dues info in the row for display
                row['prev_month_expected'] = round(pm_expected, 2)
                row['prev_month_collected'] = round(pm_collected, 2)
                row['prev_month_pending'] = pm_pending
                row['prev_month_label'] = f"{calendar.month_abbr[prev_month]} {prev_year}"
        
        # Filter to only show users with old month dues
        rows = [r for r in rows if r['user'].id in old_dues_user_ids]

    # Date range filter (filters by payment_due_date)
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    if start_date_str or end_date_str:
        from datetime import datetime as dt
        start_filter = None
        end_filter = None
        if start_date_str:
            try:
                start_filter = dt.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        if end_date_str:
            try:
                end_filter = dt.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        if start_filter or end_filter:
            def _in_date_range(r):
                pd = r.get('payment_due_date')
                if pd is None:
                    return False  # Exclude rows without payment due date
                if start_filter and pd < start_filter:
                    return False
                if end_filter and pd > end_filter:
                    return False
                return True
            rows = [r for r in rows if _in_date_range(r)]

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
        if sort_key == 'advance':
            return float(row.get('advance') or 0.0)
        if sort_key == 'status':
            return status_order.get(row.get('status'), 99)
        if sort_key == 'joining':
            # None should sort last in asc, first in desc; we encode as (is_none, value)
            j = row.get('joining')
            return (j is None, j or m_first)
        if sort_key == 'leaving':
            l = row.get('leaving')
            return (l is None, l or m_first)
        if sort_key == 'payment_date':
            # Payment date sorting: None should sort last in asc, first in desc
            pd = row.get('payment_due_date')
            return (pd is None, pd or m_first)
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
    
    # Add old dues total if filtered by old_dues
    if only == 'old_dues':
        footer_totals['prev_month_pending'] = round(sum((r.get('prev_month_pending') or 0.0) for r in rows), 2)

    # Calculate today's collection (all payments made today - both fee and advance)
    today_payments = Payment.objects.filter(
        pg=pg,
        status='success',
        date=today
    )
    today_collection_total = today_payments.aggregate(total=Sum('amount')).get('total') or 0
    today_collection_count = today_payments.count()

    summary = {
        'year': year, 'month': month,
        # Use footer_totals (post-filter) so summary numbers match the table
        'total_expected': footer_totals.get('expected', 0.0),
        'total_collected': footer_totals.get('collected', 0.0),
        'total_pending': footer_totals.get('pending', 0.0),
        'total_advance': footer_totals.get('advance', total_advance_all),
        'total_prev_month_pending': footer_totals.get('prev_month_pending', 0.0),
        'today_collection': round(float(today_collection_total), 2),
        'today_collection_count': today_collection_count,
        'counts': {
            'paid': sum(1 for r in rows if r['status'] == 'paid'),
            'partial': sum(1 for r in rows if r['status'] == 'partial'),
            'unpaid': sum(1 for r in rows if r['status'] == 'unpaid'),
            'upcoming': sum(1 for r in rows if r['status'] == 'upcoming'),
        },
        'nav': {
            'prev_year': prev_year, 'prev_month': prev_month,
            'next_year': next_year, 'next_month': next_month,
        },
        'prev_month_label': f"{calendar.month_abbr[prev_month]} {prev_year}",
    }

    return render(request, 'finance/monthly_dashboard.html', {
        'pg': pg,
        'rows': rows,
        'footer_totals': footer_totals,
        'summary': summary,
        'today': today,
        'pgs': list(_admin_pgs(request.user)),
        'm_first': m_first,
        'current_sort': sort_key,
        'current_dir': sort_dir,
        'filter_only': only,
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
@transaction.atomic
def referral_credit_apply(request, credit_id: int):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('finance_monthly')

    credit = get_object_or_404(
        ReferralCredit.objects.select_related('pg', 'referrer_user'),
        pk=credit_id,
    )
    if not _is_authorized_pg(request.user, credit.pg_id):
        messages.error(request, "You do not have access to this PG.")
        return redirect('finance_monthly')

    today = timezone.now().date()
    try:
        year = int(request.POST.get('year') or today.year)
        month = int(request.POST.get('month') or today.month)
    except (TypeError, ValueError):
        year, month = today.year, today.month
    month_start, _, _ = _month_range(year, month)

    if credit.redeemed_on:
        if credit.redeemed_for_month == month_start:
            messages.info(request, "Referral credit already applied for this month.")
        else:
            applied_month = credit.redeemed_for_month.strftime('%B %Y') if credit.redeemed_for_month else 'a previous month'
            messages.info(request, f"Referral credit was already applied in {applied_month}.")
    else:
        credit.redeemed_on = timezone.now()
        credit.redeemed_for_month = month_start
        credit.redeemed_amount = credit.amount
        if not credit.scheduled_month:
            credit.scheduled_month = month_start
        credit.save(update_fields=['redeemed_on', 'redeemed_for_month', 'redeemed_amount', 'scheduled_month', 'updated_at'])
        log(
            request.user,
            'referral_credit_applied',
            'ReferralCredit',
            credit.id,
            message=f"Applied referral credit of ₹{credit.amount} for {credit.referrer_user_id} ({month_start})",
        )
        messages.success(request, f"Referral credit of ₹{credit.amount} applied for {month_start.strftime('%B %Y')}.")

    params = []
    for key in ('year', 'month', 'pg', 'sort', 'dir', 'only'):
        val = request.POST.get(key)
        if val not in (None, ''):
            params.append((key, val))
    redirect_url = reverse('finance_monthly')
    if params:
        redirect_url = f"{redirect_url}?{urlencode(params)}"
    return redirect(redirect_url)


@login_required
@transaction.atomic
def referral_credit_remove(request, credit_id: int):
    """Undo an applied referral credit for the month (unapply) or clear scheduling.

    This keeps the ReferralCredit record for audit but clears the redeemed_* fields so
    it no longer affects monthly calculations.
    """
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('finance_monthly')

    credit = get_object_or_404(ReferralCredit, pk=credit_id)
    if not _is_authorized_pg(request.user, credit.pg_id):
        messages.error(request, "You do not have access to this PG.")
        return redirect('finance_monthly')

    # If not redeemed, nothing to remove — provide feedback
    if not credit.redeemed_on and not credit.redeemed_for_month:
        messages.info(request, "Referral credit is not applied; nothing to remove.")
    else:
        # Clear redeemed fields but keep scheduled_month as-is (so admin can reschedule/apply later)
        credit.redeemed_on = None
        credit.redeemed_for_month = None
        credit.redeemed_amount = None
        credit.save(update_fields=['redeemed_on', 'redeemed_for_month', 'redeemed_amount', 'updated_at'])
        log(request.user, 'referral_credit_removed', 'ReferralCredit', credit.id, message=f"Unapplied referral credit {credit.id} for PG {credit.pg_id}")
        messages.success(request, "Referral credit unapplied.")

    params = []
    for key in ('year', 'month', 'pg', 'sort', 'dir', 'only'):
        val = request.POST.get(key)
        if val not in (None, ''):
            params.append((key, val))
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
        # Exclude users who have left on or before their payment due date for this month.
        # If due_date is on/after latest_end, skip exporting their data for this month.
        if latest_end and due_date and due_date >= latest_end:
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
        # Exclude segment rows where the booking's payment due date is on/after the leaving date
        due_date = _payment_due_for_month(b, m_first, m_days)
        if b.leaving_date and due_date and due_date >= b.leaving_date:
            continue
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
        # Exclude tenants who left on or before the payment due date for this month.
        # If the computed payment due date falls on/after the latest segment end, skip this user.
        ends = [seg['end'] for seg in segs if seg.get('end')]
        latest_end = max(ends) if ends else None
        if latest_end and due_date and due_date >= latest_end:
            continue

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
            'payment_due': due_date,
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
        p.drawString(joining_col_x, y, "Payment Date")
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
            # Show payment date (computed per-user) if available, otherwise fall back to segment start
            row_due = row.get('payment_due')
            if row_due:
                p.drawString(joining_col_x, y, row_due.strftime('%Y-%m-%d'))
            else:
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
        # Exclude tenants who left on or before the payment due date for this month.
        ends = [seg['end'] for seg in segments if seg.get('end')]
        latest_end = max(ends) if ends else None
        if latest_end and due_date and due_date >= latest_end:
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
            'payment_due': due_date,
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

    # Tenant-wise Details (Tenant Name, Room, Payment Date, Leaving Date, Base Rent, Days Stayed, Expected, Collected, Pending)
    ws_tenants.append(['Tenant Name', 'Phone', 'Room', 'Payment Date', 'Leaving Date', 'Base Rent', 'Days Stayed', 'Expected', 'Collected', 'Pending'])
    for entry in entries:
        u = entry['user']
        tenant_name = (f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email)
        phone_val = entry.get('phone') or ''
        for seg in entry['segments']:
            start = entry.get('payment_due') or seg['start']
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
        # Credit increases tenant's balance, debit/deposit_deduction decreases it
        if a.type == 'credit':
            credit = float(a.amount)
            debit = 0.0
        else:  # debit or deposit_deduction
            credit = 0.0
            debit = float(a.amount)
        items.append({
            'date': a.date,
            'type': f'adjustment/{a.type}',
            'description': a.notes or a.get_type_display(),
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
        # Credit increases tenant's balance, debit/deposit_deduction decreases it
        if a.type == 'credit':
            credit = float(a.amount); debit = 0.0
        else:  # debit or deposit_deduction
            credit = 0.0; debit = float(a.amount)
        entries.append({
            'date': a.date,
            'type': f'adjustment/{a.type}',
            'description': a.notes or a.get_type_display(),
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
    if mode not in ('upi', 'cash', 'upi_cash'):
        mode = 'upi'

    # Handle UPI+CASH mode: extract component amounts
    upi_amount_val = None
    cash_amount_val = None
    if mode == 'upi_cash':
        upi_raw = (request.POST.get('upi_amount') or '').strip()
        cash_raw = (request.POST.get('cash_amount') or '').strip()
        try:
            if upi_raw:
                upi_amount_val = Decimal(upi_raw)
        except (InvalidOperation, TypeError):
            upi_amount_val = None
        try:
            if cash_raw:
                cash_amount_val = Decimal(cash_raw)
        except (InvalidOperation, TypeError):
            cash_amount_val = None

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

    from_date_str = (request.POST.get('from_date') or '').strip()
    to_date_str = (request.POST.get('to_date') or '').strip()
    from_date_val = parse_date(from_date_str) if from_date_str else None
    to_date_val = parse_date(to_date_str) if to_date_str else None
    if not from_date_val:
        from_date_val = pay_date
    _, default_to_date = _billing_period_from_payment_date(from_date_val)
    if not to_date_val:
        to_date_val = default_to_date
    elif from_date_val and to_date_val < from_date_val:
        to_date_val = default_to_date or to_date_val
    if not to_date_val:
        # final guard: ensure to_date always present
        _, to_date_val = _billing_period_from_payment_date(pay_date)

    # If payment type is 'advance', ignore any posted billing from/to (server authoritative)
    if ptype == 'advance':
        from_date_val = None
        to_date_val = None

    # Create Payment (success by default)
    try:
        # Server-side validation for UPI+CASH: require the split and ensure sum equals amount
        if mode == 'upi_cash':
            if upi_amount_val is None or cash_amount_val is None:
                raise ValueError('Both UPI and Cash amounts are required for UPI+CASH mode.')
            if (upi_amount_val + cash_amount_val) != amount:
                raise ValueError('UPI amount plus Cash amount must equal the total Amount.')

        payment = Payment.objects.create(
            user=u, pg=pg, amount=amount, date=pay_date,
            status='success', mode=mode, type=ptype, notes=notes,
            from_date=from_date_val, to_date=to_date_val,
            upi_amount=upi_amount_val, cash_amount=cash_amount_val,
        )
        
        # If payment type is 'advance', update the user's booking based on payment date
        if ptype == 'advance':
            try:
                # Find the booking where payment date falls within the booking period
                # Payment date should be:
                # - After or equal to joining_date
                # - Before or equal to leaving_date (if leaving_date exists)
                # - If leaving_date is null, payment just needs to be after joining_date
                
                matching_booking = Booking.objects.filter(
                    user=u,
                    room__pg=pg,
                    status=Booking.APPROVED,
                    joining_date__lte=pay_date,  # Payment date is on or after joining
                ).filter(
                    Q(leaving_date__isnull=True) |  # No leaving date yet (current booking)
                    Q(leaving_date__gte=pay_date)   # Or payment date is before/on leaving date
                ).select_related('room').first()
                
                if matching_booking:
                    # Add the payment amount to existing advance_paid
                    matching_booking.advance_paid += amount
                    matching_booking.save(update_fields=['advance_paid'])
                    
                    # Log the update
                    from core.audit import log
                    log(
                        user=request.user,
                        pg=pg,
                        action='advance_payment_added',
                        model='Booking',
                        object_id=matching_booking.id,
                        message=f"Advance payment of ₹{amount} added to booking (payment date: {pay_date}). Total advance: ₹{matching_booking.advance_paid}"
                    )
            except Exception as e:
                # Don't fail the payment creation if booking update fails
                # Just log the error for debugging
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to update booking advance_paid: {e}")
                
    except Exception as e:
        messages.error(request, f'Failed to create payment: {e}')
        # AJAX error
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
            return JsonResponse({'ok': False, 'message': f'Failed to create payment: {e}'})
        return redirect('finance_monthly')

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


# ============================================================================
# MONTHLY ADJUSTMENTS (DISCOUNTS & INCREMENTS)
# ============================================================================

@login_required
def monthly_adjustments_list(request):
    """List all monthly adjustments (discounts/increments) for the active PG."""
    if not _require_pg_admin(request.user):
        messages.error(request, 'PG Admin access required.')
        return redirect('dashboard')
    
    pg = _active_pg(request)
    if not pg:
        messages.error(request, 'No PG selected.')
        return redirect('dashboard')
    
    from datetime import date
    today = date.today()
    current_month_str = today.strftime('%Y-%m')
    
    # NOTE: We do NOT auto-deactivate adjustments anymore.
    # Instead, we compute a display_status ('Active' or 'Completed') for the frontend.
    # Backend is_active always stays True unless manually changed.
    
    # Get all adjustments for this PG
    adjustments_qs = MonthlyAdjustment.objects.filter(pg=pg).select_related('user', 'user__profile', 'created_by').order_by('-created_at')
    
    # Apply filters
    search_query = request.GET.get('search', '').strip()
    adjustment_type_filter = request.GET.get('adjustment_type', '')
    duration_type_filter = request.GET.get('duration_type', '')
    status_filter = request.GET.get('status', '')  # Changed from is_active to status
    
    if search_query:
        adjustments_qs = adjustments_qs.filter(
            Q(user__email__icontains=search_query) |
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query) |
            Q(notes__icontains=search_query)
        )
    
    if adjustment_type_filter:
        adjustments_qs = adjustments_qs.filter(adjustment_type=adjustment_type_filter)
    
    if duration_type_filter:
        adjustments_qs = adjustments_qs.filter(duration_type=duration_type_filter)
    
    # Status filter: 'active' means ongoing/not completed, 'completed' means all months passed
    # We filter in Python after computing display_status since it's computed, not a DB field
    
    # Sorting
    sort_by = request.GET.get('sort', '-created_at')
    valid_sort_fields = ['user__email', 'user__first_name', 'adjustment_type', 'amount', 'duration_type', 'is_active', 'created_at', '-user__email', '-user__first_name', '-adjustment_type', '-amount', '-duration_type', '-is_active', '-created_at']
    if sort_by in valid_sort_fields:
        adjustments_qs = adjustments_qs.order_by(sort_by)
    
    # Enrich adjustments with additional data for display
    from bookings.models import Booking
    enriched_adjustments = []
    for adj in adjustments_qs:
        # Get user's current booking info
        booking = Booking.objects.filter(
            user=adj.user, 
            room__pg=pg, 
            status=Booking.APPROVED
        ).select_related('room').first()
        
        room_info = f"Room {booking.room.room_no}, Bed {booking.share_no}" if booking else "No active booking"
        
        # Calculate applied and pending months
        applied_months = []
        pending_months = []
        
        # Compute display_status: 'Completed' if all selected months passed, 'Active' otherwise
        # For 'continuous' type, it's always 'Active' (never completes)
        display_status = 'Active'
        if adj.duration_type in ['one_month', 'multiple_months'] and adj.selected_months:
            for m in adj.selected_months:
                if m < current_month_str:
                    applied_months.append(m)
                else:
                    pending_months.append(m)
            # If all months are applied (none pending), it's completed
            if len(pending_months) == 0 and len(applied_months) > 0:
                display_status = 'Completed'
        elif adj.duration_type == 'continuous':
            pending_months = ['Ongoing (Continuous)']
            display_status = 'Active'  # Continuous is never completed
        
        enriched_adjustments.append({
            'adjustment': adj,
            'room_info': room_info,
            'applied_months': applied_months,
            'pending_months': pending_months,
            'display_status': display_status,
        })
    
    # Apply status filter after computing display_status
    if status_filter:
        if status_filter.lower() == 'active':
            enriched_adjustments = [item for item in enriched_adjustments if item['display_status'] == 'Active']
        elif status_filter.lower() == 'completed':
            enriched_adjustments = [item for item in enriched_adjustments if item['display_status'] == 'Completed']
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(enriched_adjustments, 25)
    page_num = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_num)
    
    # Calculate summary amounts for the three cards
    # Card 1: This month's amounts (adjustments that apply to current month)
    # Card 2: Completed amounts (adjustments for months that have passed)
    # Card 3: Remaining/Future amounts (adjustments for future months)
    
    this_month_discounts = Decimal('0')
    this_month_increments = Decimal('0')
    completed_discounts = Decimal('0')
    completed_increments = Decimal('0')
    remaining_discounts = Decimal('0')
    remaining_increments = Decimal('0')
    
    for adj in adjustments_qs:
        amount = adj.amount or Decimal('0')
        is_discount = adj.adjustment_type == 'discount'
        
        if adj.duration_type == 'continuous':
            # Continuous applies to current month and all future months
            if is_discount:
                this_month_discounts += amount
                remaining_discounts += amount  # Will keep applying
            else:
                this_month_increments += amount
                remaining_increments += amount
        elif adj.duration_type in ['one_month', 'multiple_months'] and adj.selected_months:
            for m in adj.selected_months:
                if m == current_month_str:
                    # This month
                    if is_discount:
                        this_month_discounts += amount
                    else:
                        this_month_increments += amount
                elif m < current_month_str:
                    # Completed (past months)
                    if is_discount:
                        completed_discounts += amount
                    else:
                        completed_increments += amount
                else:
                    # Future months (remaining)
                    if is_discount:
                        remaining_discounts += amount
                    else:
                        remaining_increments += amount
    
    # Check if user is admin (superuser or website admin)
    is_website_admin = hasattr(request.user, 'profile') and getattr(request.user.profile, 'is_website_admin', False)
    is_admin = request.user.is_superuser or is_website_admin
    
    context = {
        'pg': pg,
        'pgs': list(_admin_pgs(request.user)),
        'page_obj': page_obj,
        'adjustments': page_obj.object_list,
        'this_month_discounts': this_month_discounts,
        'this_month_increments': this_month_increments,
        'completed_discounts': completed_discounts,
        'completed_increments': completed_increments,
        'remaining_discounts': remaining_discounts,
        'remaining_increments': remaining_increments,
        'current_month_display': datetime.now().strftime('%B %Y'),
        'search_query': search_query,
        'adjustment_type_filter': adjustment_type_filter,
        'duration_type_filter': duration_type_filter,
        'status_filter': status_filter,
        'sort_by': sort_by,
        'is_admin': is_admin,
    }
    
    return render(request, 'finance/monthly_adjustments_list.html', context)


@login_required
def monthly_adjustment_add(request):
    """Render the add adjustment page."""
    if not _require_pg_admin(request.user):
        messages.error(request, 'PG Admin access required.')
        return redirect('monthly_adjustments_list')
    
    pg = _active_pg(request)
    if not pg:
        messages.error(request, 'Please select a PG first.')
        return redirect('home')
    
    context = {
        'pg': pg,
        'residents_api_url': reverse('monthly_adjustments_residents_api'),
    }
    
    return render(request, 'finance/monthly_adjustment_add.html', context)


@login_required
@transaction.atomic
def monthly_adjustment_create(request):
    """Create a new monthly adjustment."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'PG Admin access required.'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG selected.'}, status=400)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    
    # Get form data directly from POST
    user_id = request.POST.get('user')
    adjustment_type = request.POST.get('adjustment_type')
    amount = request.POST.get('amount')
    duration_type = request.POST.get('duration_type')
    selected_months_str = request.POST.get('selected_months', '')
    # is_active is always True by default - we don't allow setting it to False on creation
    is_active = True
    notes = request.POST.get('notes', '')
    
    # Validate required fields
    errors = {}
    if not user_id:
        errors['user'] = 'Resident is required.'
    if not adjustment_type:
        errors['adjustment_type'] = 'Adjustment type is required.'
    if not amount:
        errors['amount'] = 'Amount is required.'
    if not duration_type:
        errors['duration_type'] = 'Duration type is required.'
    
    # Validate amount
    try:
        amount = Decimal(amount)
        if amount <= 0:
            errors['amount'] = 'Amount must be greater than 0.'
    except (ValueError, TypeError, InvalidOperation):
        errors['amount'] = 'Invalid amount.'
    
    # Validate user exists and is a resident of this PG
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        from bookings.models import Booking
        from datetime import date
        from django.db.models import Q
        today = date.today()
        has_active_booking = Booking.objects.filter(
            user=user,
            room__pg=pg,
            status=Booking.APPROVED
        ).filter(
            Q(leaving_date__isnull=True) | Q(leaving_date__gte=today)
        ).exists()
        if not has_active_booking:
            errors['user'] = 'Selected user is not an active resident of this PG.'
    except User.DoesNotExist:
        errors['user'] = 'Invalid user.'
    
    # Process selected_months based on duration_type
    selected_months = []
    if duration_type in ['one_month', 'multiple_months']:
        if selected_months_str:
            selected_months = [m.strip() for m in selected_months_str.split(',') if m.strip()]
        if not selected_months:
            errors['selected_months'] = 'Please select at least one month.'
    
    if errors:
        return JsonResponse({'error': 'Validation failed.', 'errors': errors}, status=400)
    
    # Create the adjustment
    adjustment = MonthlyAdjustment.objects.create(
        user=user,
        pg=pg,
        adjustment_type=adjustment_type,
        amount=amount,
        duration_type=duration_type,
        selected_months=selected_months,
        is_active=is_active,
        created_by=request.user,
        notes=notes
    )
    
    log(request.user, 'create', 'MonthlyAdjustment', adjustment.id, f'Created {adjustment.adjustment_type} of ₹{adjustment.amount} for {adjustment.user.email}')
    
    return JsonResponse({
        'success': True,
        'message': f'{adjustment.get_adjustment_type_display()} created successfully.',
        'adjustment_id': adjustment.id
    })


@login_required
@transaction.atomic
def monthly_adjustment_edit(request, pk):
    """Edit an existing monthly adjustment."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'PG Admin access required.'}, status=403)
    
    adjustment = get_object_or_404(MonthlyAdjustment, pk=pk)
    
    if not _is_authorized_pg(request.user, adjustment.pg.id):
        return JsonResponse({'error': 'Unauthorized PG.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    
    # Get form data directly from POST
    adjustment_type = request.POST.get('adjustment_type')
    amount = request.POST.get('amount')
    duration_type = request.POST.get('duration_type')
    selected_months_str = request.POST.get('selected_months', '')
    is_active = request.POST.get('is_active') == 'on'
    notes = request.POST.get('notes', '')
    
    # Validate required fields
    errors = {}
    if not adjustment_type:
        errors['adjustment_type'] = 'Adjustment type is required.'
    if not amount:
        errors['amount'] = 'Amount is required.'
    if not duration_type:
        errors['duration_type'] = 'Duration type is required.'
    
    # Validate amount
    try:
        amount = Decimal(amount)
        if amount <= 0:
            errors['amount'] = 'Amount must be greater than 0.'
    except (ValueError, TypeError, InvalidOperation):
        errors['amount'] = 'Invalid amount.'
    
    # Process selected_months based on duration_type
    selected_months = []
    if duration_type in ['one_month', 'multiple_months']:
        if selected_months_str:
            selected_months = [m.strip() for m in selected_months_str.split(',') if m.strip()]
        if not selected_months:
            errors['selected_months'] = 'Please select at least one month.'
    
    if errors:
        return JsonResponse({'error': 'Validation failed.', 'errors': errors}, status=400)
    
    # Update the adjustment
    adjustment.adjustment_type = adjustment_type
    adjustment.amount = amount
    adjustment.duration_type = duration_type
    adjustment.selected_months = selected_months
    adjustment.is_active = is_active
    adjustment.notes = notes
    adjustment.save()
    
    log(request.user, 'update', 'MonthlyAdjustment', adjustment.id, f'Updated {adjustment.adjustment_type} for {adjustment.user.email}')
    
    return JsonResponse({
        'success': True,
        'message': f'{adjustment.get_adjustment_type_display()} updated successfully.',
    })


@login_required
@transaction.atomic
def monthly_adjustment_delete(request, pk):
    """Delete a monthly adjustment. Only superusers/website admins can delete."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'PG Admin access required.'}, status=403)
    
    # Only superusers or website admins can delete
    is_website_admin = hasattr(request.user, 'profile') and getattr(request.user.profile, 'is_website_admin', False)
    if not (request.user.is_superuser or is_website_admin):
        return JsonResponse({'error': 'Only administrators can delete adjustments.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    
    adjustment = get_object_or_404(MonthlyAdjustment, pk=pk)
    
    if not _is_authorized_pg(request.user, adjustment.pg.id):
        return JsonResponse({'error': 'Unauthorized PG.'}, status=403)
    
    user_email = adjustment.user.email
    adjustment_type = adjustment.get_adjustment_type_display()
    amount = adjustment.amount
    adjustment_id = adjustment.id
    
    adjustment.delete()
    
    log(request.user, 'delete', 'MonthlyAdjustment', adjustment_id, f'Deleted {adjustment_type} of ₹{amount} for {user_email}')
    
    return JsonResponse({'success': True, 'message': f'{adjustment_type} deleted successfully.'})


@login_required
@transaction.atomic
def monthly_adjustment_toggle(request, pk):
    """Toggle active status of a monthly adjustment. Only superusers/website admins can toggle."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'PG Admin access required.'}, status=403)
    
    # Only superusers or website admins can toggle
    is_website_admin = hasattr(request.user, 'profile') and getattr(request.user.profile, 'is_website_admin', False)
    if not (request.user.is_superuser or is_website_admin):
        return JsonResponse({'error': 'Only administrators can toggle adjustments.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    
    adjustment = get_object_or_404(MonthlyAdjustment, pk=pk)
    
    if not _is_authorized_pg(request.user, adjustment.pg.id):
        return JsonResponse({'error': 'Unauthorized PG.'}, status=403)
    
    adjustment.is_active = not adjustment.is_active
    adjustment.save(update_fields=['is_active'])
    
    status_text = 'activated' if adjustment.is_active else 'deactivated'
    log(request.user, 'update', 'MonthlyAdjustment', adjustment.id, f'{adjustment.get_adjustment_type_display()} {status_text} for {adjustment.user.email}')
    
    return JsonResponse({
        'success': True,
        'message': f'Adjustment {status_text} successfully.',
        'is_active': adjustment.is_active
    })


@login_required
def monthly_adjustments_residents_api(request):
    """API endpoint to get list of residents for a PG (for the adjustment form)."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'PG Admin access required.'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG selected.'}, status=400)
    
    # Get current active residents (approved bookings who haven't left)
    from bookings.models import Booking
    from django.db.models import Q
    from datetime import date
    
    today = date.today()
    resident_bookings = Booking.objects.filter(
        status=Booking.APPROVED,
        room__pg=pg
    ).filter(
        # Include only if: no leaving_date OR leaving_date is today or in the future
        Q(leaving_date__isnull=True) | Q(leaving_date__gte=today)
    ).select_related('user', 'room').order_by('room__room_no', 'share_no')
    
    residents = []
    for booking in resident_bookings:
        user = booking.user
        name = user.get_full_name() or user.email
        residents.append({
            'id': user.id,
            'name': name,
            'email': user.email,
            'room': f"Room {booking.room.room_no}, Bed {booking.share_no}",
            'display': f"{name} - Room {booking.room.room_no}, Bed {booking.share_no}"
        })
    
    return JsonResponse({'residents': residents})

