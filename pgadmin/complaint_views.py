"""
PG Admin complaint management views
Admins can view all complaints, filter/sort, add comments, change status
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q, Count, Prefetch
from django.utils import timezone
from django.http import JsonResponse
from datetime import datetime, timedelta
from .models import PG, PGAdmin, Complaint, ComplaintComment
from .complaint_notifications import notify_admin_comment
from core.push_notifications import send_push_to_user


def _require_pg_admin(user):
    """Check if user is a PG admin"""
    return PGAdmin.objects.filter(user=user).exists()


def _admin_pgs(user):
    """Get all PGs managed by this admin"""
    return PG.objects.filter(admins__user=user)


def _sync_active_pg_session(request, pg_id):
    """Persist active PG in session when request provides a valid admin PG context."""
    try:
        pg_id_int = int(pg_id)
    except (TypeError, ValueError):
        return

    if _admin_pgs(request.user).filter(id=pg_id_int).exists():
        request.session['active_pg_id'] = pg_id_int


@login_required
def admin_complaints(request):
    """
    List all complaints for PGs managed by this admin
    Load all complaints to frontend for client-side filtering
    """
    if not _require_pg_admin(request.user):
        messages.error(request, 'PG Admin access required.')
        return redirect('profile')
    
    # Get admin's PGs
    admin_pgs = _admin_pgs(request.user)
    _sync_active_pg_session(request, request.GET.get('pg'))
    
    # Load ALL complaints from admin's PGs (no server-side filtering)
    complaints = Complaint.objects.filter(pg__in=admin_pgs).select_related(
        'user', 'pg', 'booking', 'resolved_by'
    ).prefetch_related('comments').order_by('-created_at')
    
    # Calculate stats for all complaints
    stats = {
        'total': complaints.count(),
        'open': complaints.filter(status=Complaint.OPEN).count(),
        'in_progress': complaints.filter(status=Complaint.IN_PROGRESS).count(),
        'solved': complaints.filter(status=Complaint.SOLVED).count(),
        'urgent': complaints.filter(priority=Complaint.URGENT, status__in=[Complaint.OPEN, Complaint.IN_PROGRESS]).count(),
    }
    
    context = {
        'complaints': complaints,
        'admin_pgs': admin_pgs,
        'status_choices': Complaint.STATUS_CHOICES,
        'priority_choices': Complaint.PRIORITY_CHOICES,
        'category_choices': Complaint.CATEGORY_CHOICES,
        'stats': stats,
    }
    
    return render(request, 'pgadmin/complaints/admin_complaints.html', context)


@login_required
def admin_complaint_detail(request, complaint_id):
    """
    View and manage a specific complaint
    Admin can add comments and change status
    """
    if not _require_pg_admin(request.user):
        messages.error(request, 'PG Admin access required.')
        return redirect('profile')
    
    # Get complaint and verify admin has access
    complaint = get_object_or_404(
        Complaint.objects.select_related('user', 'pg', 'booking', 'resolved_by'),
        id=complaint_id
    )
    
    admin_pgs = _admin_pgs(request.user)
    if complaint.pg not in admin_pgs:
        messages.error(request, 'You do not have access to this complaint.')
        return redirect('admin_complaints')

    _sync_active_pg_session(request, complaint.pg_id)
    
    # Get all comments (including internal)
    comments = complaint.comments.all().select_related('user').order_by('created_at')

    # Get all media files attached to complaint
    media_files = complaint.media_files.all()

    # Best-effort tenant phone for WhatsApp action
    tenant_phone = ''
    try:
        app = getattr(getattr(complaint, 'booking', None), 'application', None)
        tenant_phone = (getattr(app, 'whatsapp_number', '') or getattr(app, 'phone', '') or '').strip()
    except Exception:
        tenant_phone = ''
    if not tenant_phone:
        tenant_phone = (getattr(getattr(complaint.user, 'profile', None), 'phone', '') or '').strip()

    public_comments = comments.filter(is_internal=False)

    context = {
        'complaint': complaint,
        'comments': comments,
        'public_comments': public_comments,
        'media_files': media_files,
        'tenant_phone': tenant_phone,
        'status_choices': Complaint.STATUS_CHOICES,
    }
    
    return render(request, 'pgadmin/complaints/admin_complaint_detail.html', context)


@login_required
def admin_complaint_add_comment(request, complaint_id):
    """
    Add a comment to a complaint
    """
    if not _require_pg_admin(request.user):
        return JsonResponse({'success': False, 'error': 'PG Admin access required.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    
    # Get complaint and verify admin has access
    complaint = get_object_or_404(Complaint, id=complaint_id)
    admin_pgs = _admin_pgs(request.user)
    if complaint.pg not in admin_pgs:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)
    
    comment_text = request.POST.get('comment', '').strip()
    is_internal = request.POST.get('is_internal', 'false') == 'true'
    
    if not comment_text:
        return JsonResponse({'success': False, 'error': 'Comment text is required.'}, status=400)
    
    # Create comment
    comment = ComplaintComment.objects.create(
        complaint=complaint,
        user=request.user,
        comment=comment_text,
        is_internal=is_internal
    )
    
    # Update complaint's updated_at
    complaint.save()

    # Non-blocking email notification to tenant for public admin comments
    try:
        notify_admin_comment(comment)
    except Exception:
        pass

    if not is_internal:
        try:
            send_push_to_user(
                complaint.user,
                title=f"Complaint #{complaint.id} Updated",
                body=comment_text[:120],
                url=f"/user/complaints/{complaint.id}/",
                extra_data={'type': 'complaint_comment', 'complaint_id': complaint.id},
            )
        except Exception:
            pass
    
    messages.success(request, 'Comment added successfully.')
    
    return JsonResponse({
        'success': True,
        'comment': {
            'id': comment.id,
            'user': comment.user.get_full_name() or comment.user.email,
            'comment': comment.comment,
            'is_internal': comment.is_internal,
            'created_at': comment.created_at.strftime('%B %d, %Y at %I:%M %p'),
        }
    })


@login_required
def admin_complaint_update_status(request, complaint_id):
    """
    Update complaint status
    """
    if not _require_pg_admin(request.user):
        return JsonResponse({'success': False, 'error': 'PG Admin access required.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    
    # Get complaint and verify admin has access
    complaint = get_object_or_404(Complaint, id=complaint_id)
    admin_pgs = _admin_pgs(request.user)
    if complaint.pg not in admin_pgs:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)
    
    new_status = request.POST.get('status', '').strip()
    valid_statuses = [choice[0] for choice in Complaint.STATUS_CHOICES]
    
    if new_status not in valid_statuses:
        return JsonResponse({'success': False, 'error': 'Invalid status.'}, status=400)
    
    old_status = complaint.status
    complaint.status = new_status
    
    # If marked as solved, record resolution details
    if new_status == Complaint.SOLVED and old_status != Complaint.SOLVED:
        complaint.resolved_at = timezone.now()
        complaint.resolved_by = request.user
    
    complaint.save()
    
    messages.success(request, f'Complaint status updated to "{dict(Complaint.STATUS_CHOICES)[new_status]}".')
    
    return JsonResponse({
        'success': True,
        'status': new_status,
        'status_display': dict(Complaint.STATUS_CHOICES)[new_status],
        'badge_class': complaint.get_status_badge_class(),
    })


@login_required
def admin_complaint_update_priority(request, complaint_id):
    """
    Update complaint priority
    """
    if not _require_pg_admin(request.user):
        return JsonResponse({'success': False, 'error': 'PG Admin access required.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    
    # Get complaint and verify admin has access
    complaint = get_object_or_404(Complaint, id=complaint_id)
    admin_pgs = _admin_pgs(request.user)
    if complaint.pg not in admin_pgs:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)
    
    new_priority = request.POST.get('priority', '').strip()
    valid_priorities = [choice[0] for choice in Complaint.PRIORITY_CHOICES]
    
    if new_priority not in valid_priorities:
        return JsonResponse({'success': False, 'error': 'Invalid priority.'}, status=400)
    
    complaint.priority = new_priority
    complaint.save()
    
    messages.success(request, f'Complaint priority updated to "{dict(Complaint.PRIORITY_CHOICES)[new_priority]}".')
    
    return JsonResponse({
        'success': True,
        'priority': new_priority,
        'priority_display': dict(Complaint.PRIORITY_CHOICES)[new_priority],
        'badge_class': complaint.get_priority_badge_class(),
    })


@login_required
def admin_complaint_edit_comment(request, comment_id):
    """
    Edit a complaint comment
    """
    if not _require_pg_admin(request.user):
        return JsonResponse({'success': False, 'error': 'PG Admin access required.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    
    # Get comment and verify admin has access
    comment = get_object_or_404(ComplaintComment.objects.select_related('complaint', 'complaint__pg'), id=comment_id)
    admin_pgs = _admin_pgs(request.user)
    if comment.complaint.pg not in admin_pgs:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)
    
    comment_text = request.POST.get('comment', '').strip()
    is_internal = request.POST.get('is_internal', 'false') == 'true'
    
    if not comment_text:
        return JsonResponse({'success': False, 'error': 'Comment text is required.'}, status=400)
    
    # Update comment
    comment.comment = comment_text
    comment.is_internal = is_internal
    comment.save()
    
    # Update complaint's updated_at
    comment.complaint.save()
    
    messages.success(request, 'Comment updated successfully.')
    
    return JsonResponse({
        'success': True,
        'comment': {
            'id': comment.id,
            'comment': comment.comment,
            'is_internal': comment.is_internal,
            'updated_at': comment.updated_at.strftime('%B %d, %Y at %I:%M %p'),
        }
    })


@login_required
def admin_complaint_delete_comment(request, comment_id):
    """
    Delete a complaint comment
    """
    if not _require_pg_admin(request.user):
        return JsonResponse({'success': False, 'error': 'PG Admin access required.'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=405)
    
    # Get comment and verify admin has access
    comment = get_object_or_404(ComplaintComment.objects.select_related('complaint', 'complaint__pg'), id=comment_id)
    admin_pgs = _admin_pgs(request.user)
    if comment.complaint.pg not in admin_pgs:
        return JsonResponse({'success': False, 'error': 'Access denied.'}, status=403)
    
    # Store complaint reference before deleting
    complaint = comment.complaint
    comment.delete()
    
    # Update complaint's updated_at
    complaint.save()
    
    messages.success(request, 'Comment deleted successfully.')
    
    return JsonResponse({
        'success': True,
        'message': 'Comment deleted successfully.'
    })
