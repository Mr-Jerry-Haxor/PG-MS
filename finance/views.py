from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from pgadmin.models import PG
from .models import Fees, Payment, Expenditure
from core.audit import log
from .forms import FeesForm, PaymentForm, ExpenditureForm
from bookings.models import Booking
from django.db.models import Q


def _require_pg_admin(user):
    return hasattr(user, 'profile') and user.profile.is_pg_admin and user.profile.status == 'active'


def _admin_pgs(user):
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
            obj = form.save(commit=False)
            obj.pg = pg
            obj.save()
            log(request.user, 'payment_saved', 'Payment', obj.id)
            messages.success(request, "Payment saved.")
            return redirect('payments_list')
    else:
        form = PaymentForm(instance=instance, user_queryset=user_qs, room_map=room_map)
    return render(request, 'finance/payments_form.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})


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
