from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model

from pgadmin.models import PG, PGAdmin
from bookings.models import Booking
from finance.models import Payment, Expenditure
from pgadmin.forms import PGForm


def _require_site_admin(user):
    # Allow Django superusers as site admins
    if getattr(user, 'is_superuser', False):
        return True
    return hasattr(user, 'profile') and user.profile.is_website_admin and user.profile.status == 'active'


@login_required
def dashboard(request):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    stats = {
        'pgs': PG.objects.count(),
        'pg_admins': PGAdmin.objects.count(),
        'users': get_user_model().objects.count(),
    }
    return render(request, 'siteadmin/dashboard.html', {"stats": stats})


@login_required
def pgs(request):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    items = PG.objects.all().order_by('-created_at')
    return render(request, 'siteadmin/pgs.html', {"items": items})


@login_required
def pg_new(request):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    if request.method == 'POST':
        form = PGForm(request.POST)
        if form.is_valid():
            pg = form.save(commit=False)
            pg.created_by_admin = request.user
            pg.save()
            messages.success(request, "PG created.")
            return redirect('sa_pgs')
    else:
        form = PGForm()
    return render(request, 'siteadmin/pg_form.html', {"form": form})


@login_required
def pg_edit(request, pg_id):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    pg = get_object_or_404(PG, pk=pg_id)
    if request.method == 'POST':
        form = PGForm(request.POST, instance=pg)
        if form.is_valid():
            form.save()
            messages.success(request, "PG details updated.")
            return redirect('sa_pgs')
    else:
        form = PGForm(instance=pg)
    return render(request, 'siteadmin/pg_form.html', {"form": form, "pg": pg})


@login_required
def pg_manage_admins(request, pg_id):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    pg = get_object_or_404(PG, pk=pg_id)
    User = get_user_model()
    users = User.objects.all().select_related('profile').order_by('email')
    if request.method == 'POST':
        action = request.POST.get('action', 'add')
        if action == 'remove':
            rem_user_id = request.POST.get('remove_user_id')
            user = get_object_or_404(User, pk=rem_user_id)
            PGAdmin.objects.filter(user=user, pg=pg).delete()
            # update profile flag
            if hasattr(user, 'profile'):
                # Only clear the flag if the user is not admin of any PGs now
                still_admin = PGAdmin.objects.filter(user=user).exists()
                if user.profile.is_pg_admin != still_admin:
                    user.profile.is_pg_admin = still_admin
                    user.profile.save(update_fields=['is_pg_admin'])
            messages.info(request, "PG Admin removed.")
            return redirect('sa_pg_admins', pg_id=pg.id)
        else:
            user_id = request.POST.get('user_id')
            user = get_object_or_404(User, pk=user_id)
            PGAdmin.objects.get_or_create(user=user, pg=pg)
            # update profile flag
            if hasattr(user, 'profile'):
                user.profile.is_pg_admin = True
                user.profile.save(update_fields=['is_pg_admin'])
            messages.success(request, "PG Admin assigned.")
            return redirect('sa_pg_admins', pg_id=pg.id)
    admins = pg.admins.select_related('user').all()
    return render(request, 'siteadmin/pg_manage_admins.html', {"pg": pg, "admins": admins, "users": users})


@login_required
def users(request):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    items = get_user_model().objects.all().order_by('-date_joined')
    return render(request, 'siteadmin/users.html', {"items": items})


@login_required
def bookings(request):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    items = Booking.objects.select_related('user', 'room').all().order_by('-created_at')
    return render(request, 'siteadmin/bookings.html', {"items": items})


@login_required
def payments(request):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    items = Payment.objects.select_related('user', 'pg').all().order_by('-created_at')
    return render(request, 'siteadmin/payments.html', {"items": items})


@login_required
def expenditures(request):
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    items = Expenditure.objects.select_related('pg').all().order_by('-created_at')
    return render(request, 'siteadmin/expenditures.html', {"items": items})
