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


def _require_pg_admin(user):
    """Check if user is a PG admin"""
    return PGAdmin.objects.filter(user=user).exists()


def _admin_pgs(user):
    """Get all PGs managed by this admin"""
    return PG.objects.filter(admins__user=user)


@login_required
def admin_complaints(request):
    """
    List all complaints for PGs managed by this admin
    With filters and sorting
    """
    if not _require_pg_admin(request.user):
        messages.error(request, 'PG Admin access required.')
        return redirect('profile')
    
    # Get admin's PGs
    admin_pgs = _admin_pgs(request.user)
    
    # Get PG filter
    pg_id = request.GET.get('pg', '')
    if pg_id:
        try:
            pg = admin_pgs.get(id=pg_id)
            complaints = Complaint.objects.filter(pg=pg)
        except PG.DoesNotExist:
            messages.error(request, 'Invalid PG selected.')
            return redirect('admin_complaints')
    else:
        # All complaints from admin's PGs
        complaints = Complaint.objects.filter(pg__in=admin_pgs)
    
    # Status filter - default to 'open' and 'in_progress'
    status_filter = request.GET.get('status', 'open,in_progress')
    if status_filter and status_filter != 'all':
        # Support multiple statuses separated by comma
        if ',' in status_filter:
            status_list = [s.strip() for s in status_filter.split(',')]
            complaints = complaints.filter(status__in=status_list)
        else:
            complaints = complaints.filter(status=status_filter)
    
    # Priority filter
    priority_filter = request.GET.get('priority', '')
    if priority_filter:
        complaints = complaints.filter(priority=priority_filter)
    
    # Category filter
    category_filter = request.GET.get('category', '')
    if category_filter:
        complaints = complaints.filter(category=category_filter)
    
    # Date range filter
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
            complaints = complaints.filter(created_at__date__gte=date_from_obj)
        except ValueError:
            pass
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
            complaints = complaints.filter(created_at__date__lte=date_to_obj)
        except ValueError:
            pass
    
    # Search
    search_query = request.GET.get('search', '').strip()
    if search_query:
        complaints = complaints.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query) |
            Q(user__email__icontains=search_query)
        )
    
    # Sorting
    sort_by = request.GET.get('sort', '-created_at')
    allowed_sorts = [
        'created_at', '-created_at',
        'priority', '-priority',
        'status', '-status',
        'updated_at', '-updated_at'
    ]
    if sort_by in allowed_sorts:
        # Custom priority ordering
        if sort_by in ['priority', '-priority']:
            priority_order = {
                'urgent': 4,
                'high': 3,
                'medium': 2,
                'low': 1
            }
            complaints = list(complaints.select_related('user', 'pg', 'booking', 'resolved_by').prefetch_related('comments'))
            complaints.sort(
                key=lambda x: priority_order.get(x.priority, 0),
                reverse=(sort_by == '-priority')
            )
        else:
            complaints = complaints.order_by(sort_by).select_related('user', 'pg', 'booking', 'resolved_by').prefetch_related('comments')
    else:
        complaints = complaints.select_related('user', 'pg', 'booking', 'resolved_by').prefetch_related('comments')
    
    # Get stats
    all_complaints = Complaint.objects.filter(pg__in=admin_pgs)
    stats = {
        'total': all_complaints.count(),
        'open': all_complaints.filter(status=Complaint.OPEN).count(),
        'in_progress': all_complaints.filter(status=Complaint.IN_PROGRESS).count(),
        'solved': all_complaints.filter(status=Complaint.SOLVED).count(),
        'urgent': all_complaints.filter(priority=Complaint.URGENT, status__in=[Complaint.OPEN, Complaint.IN_PROGRESS]).count(),
    }
    
    context = {
        'complaints': complaints,
        'admin_pgs': admin_pgs,
        'current_pg': pg_id,
        'status_choices': Complaint.STATUS_CHOICES,
        'priority_choices': Complaint.PRIORITY_CHOICES,
        'category_choices': Complaint.CATEGORY_CHOICES,
        'current_status': status_filter,
        'current_priority': priority_filter,
        'current_category': category_filter,
        'current_sort': sort_by,
        'date_from': date_from,
        'date_to': date_to,
        'search_query': search_query,
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
    
    # Get all comments (including internal)
    comments = complaint.comments.all().select_related('user').order_by('created_at')
    
    context = {
        'complaint': complaint,
        'comments': comments,
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
