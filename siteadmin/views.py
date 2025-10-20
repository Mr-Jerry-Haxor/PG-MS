from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db import transaction

from pgadmin.models import PG, PGAdmin
from bookings.models import Booking, ResidentApplication, ApplicationStatusHistory
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


@login_required
def applications(request):
    """Site Admin view: all resident applications with PG filter and bulk refill action."""
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    
    # Get all PGs for filter dropdown
    all_pgs = PG.objects.all().order_by('name')
    
    # Filter by PG if specified
    pg_id = request.GET.get('pg')
    selected_pg = None
    if pg_id:
        selected_pg = all_pgs.filter(id=pg_id).first()
    
    # Query applications
    apps_qs = ResidentApplication.objects.select_related(
        'user', 'booking', 'pg', 'room', 'user__profile'
    ).all()
    
    if selected_pg:
        apps_qs = apps_qs.filter(pg=selected_pg)
    
    apps_qs = apps_qs.order_by('-created_at')
    
    context = {
        'applications': apps_qs,
        'all_pgs': all_pgs,
        'selected_pg': selected_pg,
    }
    return render(request, 'siteadmin/applications.html', context)


@login_required
@transaction.atomic
def bulk_refill_applications(request):
    """Bulk action: change selected applications to refill status (no email sent)."""
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    
    if request.method != 'POST':
        return redirect('siteadmin_applications')
    
    # Get selected application IDs from form
    app_ids = request.POST.getlist('application_ids')
    if not app_ids:
        messages.warning(request, "No applications selected.")
        return redirect('siteadmin_applications')
    
    # Update applications to refill_requested status
    updated_count = 0
    for app_id in app_ids:
        try:
            app = ResidentApplication.objects.get(id=app_id)
            # Only update if not already in refill_requested status
            if app.status != ResidentApplication.REFILL_REQUESTED:
                old_status = app.status
                app.status = ResidentApplication.REFILL_REQUESTED
                app.save(update_fields=['status'])
                
                # Create status history record
                ApplicationStatusHistory.objects.create(
                    application=app,
                    status=ResidentApplication.REFILL_REQUESTED,
                    comment=f'Bulk action by super admin (changed from {old_status})',
                    by_user=request.user
                )
                updated_count += 1
        except ResidentApplication.DoesNotExist:
            continue
    
    if updated_count > 0:
        messages.success(request, f"Successfully updated {updated_count} application(s) to refill status.")
    else:
        messages.info(request, "No applications were updated (may already be in refill status).")
    
    # Redirect back to applications page with same PG filter
    pg_id = request.GET.get('pg', '')
    if pg_id:
        return redirect(f"{request.path.replace('/bulk-refill/', '/')}?pg={pg_id}")
    return redirect('siteadmin_applications')
