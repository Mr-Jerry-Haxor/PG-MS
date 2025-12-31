from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import json

from pgadmin.models import PG, PGAdmin, PGAdminPermission
from bookings.models import Booking, ResidentApplication, ApplicationStatusHistory, RoomShareStatus, RoomSwap, ReferralCredit
from finance.models import Payment, Expenditure
from pgadmin.forms import PGForm
from core.drive import drive_delete, extract_drive_file_id
from core.audit import log


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
            pg_admin, created = PGAdmin.objects.get_or_create(user=user, pg=pg)
            # Create default permissions for new admin
            if created:
                PGAdminPermission.get_or_create_for_admin(pg_admin)
            # update profile flag
            if hasattr(user, 'profile'):
                user.profile.is_pg_admin = True
                user.profile.save(update_fields=['is_pg_admin'])
            messages.success(request, "PG Admin assigned.")
            return redirect('sa_pg_admins', pg_id=pg.id)
    
    # Get admins with their permissions
    admins = pg.admins.select_related('user', 'permissions').all()
    # Ensure all admins have permission records
    for admin in admins:
        if not hasattr(admin, 'permissions') or admin.permissions is None:
            PGAdminPermission.get_or_create_for_admin(admin)
    # Refresh the queryset to get updated permissions
    admins = pg.admins.select_related('user', 'permissions').all()
    
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


@login_required
def pg_delete(request, pg_id):
    """Delete a PG and all associated data with double confirmation."""
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    
    pg = get_object_or_404(PG, pk=pg_id)
    
    if request.method == 'GET':
        # Show confirmation page
        # Gather stats about what will be deleted
        from bookings.models import Room
        from pgadmin.models import Complaint, ComplaintComment
        from employee.models import Employee, EmployeeLedger
        from finance.models import MonthlyAdjustment, ResidentRate
        
        stats = {
            'rooms': Room.objects.filter(pg=pg).count(),
            'bookings': Booking.objects.filter(room__pg=pg).count(),
            'applications': ResidentApplication.objects.filter(pg=pg).count(),
            'payments': Payment.objects.filter(pg=pg).count(),
            'expenditures': Expenditure.objects.filter(pg=pg).count(),
            'admins': PGAdmin.objects.filter(pg=pg).count(),
            'complaints': Complaint.objects.filter(pg=pg).count(),
            'employees': Employee.objects.filter(pg=pg).count(),
            'room_statuses': RoomShareStatus.objects.filter(room__pg=pg).count(),
            'room_swaps': RoomSwap.objects.filter(Q(from_room__pg=pg) | Q(to_room__pg=pg)).count(),
            'referral_credits': ReferralCredit.objects.filter(pg=pg).count(),
        }
        
        return render(request, 'siteadmin/pg_delete_confirm.html', {
            'pg': pg,
            'stats': stats,
        })
    
    elif request.method == 'POST':
        # Process deletion
        confirmation_name = request.POST.get('confirmation_name', '').strip()
        
        if confirmation_name != pg.name:
            messages.error(request, "PG name does not match. Deletion cancelled.")
            return redirect('sa_pg_delete', pg_id=pg.id)
        
        try:
            with transaction.atomic():
                deleted_stats = _delete_pg_and_all_data(pg, request.user)
                messages.success(request, f"PG '{pg.name}' and all associated data have been deleted successfully. "
                               f"Deleted: {deleted_stats['bookings']} bookings, {deleted_stats['applications']} applications, "
                               f"{deleted_stats['payments']} payments, {deleted_stats['drive_files']} drive files.")
                return redirect('sa_pgs')
        except Exception as e:
            messages.error(request, f"Error deleting PG: {str(e)}")
            return redirect('sa_pg_delete', pg_id=pg.id)
    
    return redirect('sa_pgs')


def _delete_pg_and_all_data(pg, user):
    """Delete a PG and all its associated data including drive files."""
    from bookings.models import Room
    from pgadmin.models import Complaint, ComplaintComment
    from employee.models import Employee, EmployeeLedger
    from finance.models import MonthlyAdjustment, ResidentRate
    
    stats = {
        'bookings': 0,
        'applications': 0,
        'payments': 0,
        'drive_files': 0,
        'rooms': 0,
        'expenditures': 0,
    }
    
    # 1. Delete drive files from applications (photos and documents)
    applications = ResidentApplication.objects.filter(pg=pg)
    for app in applications:
        # Delete selfie
        if app.selfie_url:
            file_id = extract_drive_file_id(app.selfie_url)
            if file_id and drive_delete(file_id):
                stats['drive_files'] += 1
        
        # Delete aadhaar file
        if app.aadhaar_file_url:
            file_id = extract_drive_file_id(app.aadhaar_file_url)
            if file_id and drive_delete(file_id):
                stats['drive_files'] += 1
        
        # Delete aadhaar file 2 (if exists)
        if app.aadhaar_file_url_2:
            file_id = extract_drive_file_id(app.aadhaar_file_url_2)
            if file_id and drive_delete(file_id):
                stats['drive_files'] += 1
    
    stats['applications'] = applications.count()
    
    # 2. Delete bookings (this will cascade to applications via OneToOne)
    rooms = Room.objects.filter(pg=pg)
    bookings = Booking.objects.filter(room__in=rooms)
    stats['bookings'] = bookings.count()
    
    # 3. Delete payments
    payments = Payment.objects.filter(pg=pg)
    stats['payments'] = payments.count()
    payments.delete()
    
    # 4. Delete expenditures
    expenditures = Expenditure.objects.filter(pg=pg)
    stats['expenditures'] = expenditures.count()
    expenditures.delete()
    
    # 5. Delete employee ledger entries and employees
    employees = Employee.objects.filter(pg=pg)
    for emp in employees:
        EmployeeLedger.objects.filter(employee=emp).delete()
    employees.delete()
    
    # 6. Delete complaints and comments
    complaints = Complaint.objects.filter(pg=pg)
    for comp in complaints:
        ComplaintComment.objects.filter(complaint=comp).delete()
    complaints.delete()
    
    # 7. Delete referral credits
    ReferralCredit.objects.filter(pg=pg).delete()
    
    # 8. Delete monthly adjustments
    MonthlyAdjustment.objects.filter(pg=pg).delete()
    
    # 9. Delete resident rates
    ResidentRate.objects.filter(pg=pg).delete()
    
    # 10. Delete room share statuses
    RoomShareStatus.objects.filter(room__in=rooms).delete()
    
    # 11. Delete room swaps
    RoomSwap.objects.filter(Q(from_room__in=rooms) | Q(to_room__in=rooms)).delete()
    
    # 12. Delete bookings (cascade will handle applications)
    bookings.delete()
    
    # 13. Delete rooms
    stats['rooms'] = rooms.count()
    rooms.delete()
    
    # 14. Delete PG admins (this will cascade permission records)
    PGAdmin.objects.filter(pg=pg).delete()
    
    # 15. Log the deletion
    pg_name = pg.name
    pg_id = pg.id
    
    # 16. Finally delete the PG itself
    pg.delete()
    
    # Log the action
    try:
        log(user, 'pg_deleted', 'PG', pg_id, details={'name': pg_name, 'stats': stats})
    except Exception:
        pass
    
    return stats


@login_required
def pg_admin_permissions(request, pg_id, admin_id):
    """Manage permissions for a specific PG admin."""
    if not _require_site_admin(request.user):
        messages.error(request, "Website Admin access required.")
        return redirect('dashboard')
    
    pg = get_object_or_404(PG, pk=pg_id)
    pg_admin = get_object_or_404(PGAdmin, pk=admin_id, pg=pg)
    
    # Get or create permissions
    permissions = PGAdminPermission.get_or_create_for_admin(pg_admin)
    
    if request.method == 'POST':
        # Update permissions from form
        permissions.can_view_employees = request.POST.get('can_view_employees') == 'on'
        permissions.can_delete_payments = request.POST.get('can_delete_payments') == 'on'
        permissions.can_edit_payments = request.POST.get('can_edit_payments') == 'on'
        permissions.save()
        
        messages.success(request, f"Permissions updated for {pg_admin.user.email}.")
        return redirect('sa_pg_admins', pg_id=pg.id)
    
    return render(request, 'siteadmin/pg_admin_permissions.html', {
        'pg': pg,
        'pg_admin': pg_admin,
        'permissions': permissions,
    })


@login_required
@require_POST
def pg_admin_permissions_api(request, admin_id):
    """API endpoint to update a single permission via AJAX."""
    if not _require_site_admin(request.user):
        return JsonResponse({'error': 'Website Admin access required.'}, status=403)
    
    try:
        data = json.loads(request.body)
        permission_name = data.get('permission')
        value = data.get('value', False)
        
        pg_admin = get_object_or_404(PGAdmin, pk=admin_id)
        permissions = PGAdminPermission.get_or_create_for_admin(pg_admin)
        
        valid_permissions = ['can_view_employees', 'can_edit_employees', 'can_delete_payments', 'can_edit_payments', 'can_edit_applications']
        if permission_name not in valid_permissions:
            return JsonResponse({'error': 'Invalid permission name.'}, status=400)
        
        setattr(permissions, permission_name, bool(value))
        permissions.save()
        
        return JsonResponse({'success': True, 'permission': permission_name, 'value': value})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
