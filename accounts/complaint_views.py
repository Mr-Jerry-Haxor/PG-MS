"""
User-facing complaint views
Users can view their complaints, create new ones, and view details
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from pgadmin.models import Complaint, ComplaintComment
from bookings.models import Booking


@login_required
def my_complaints(request):
    """
    List all complaints raised by the logged-in user
    """
    # Get all complaints for this user
    complaints = Complaint.objects.filter(user=request.user).select_related(
        'pg', 'booking', 'resolved_by'
    ).prefetch_related('comments')
    
    # Filter by status if provided
    status_filter = request.GET.get('status', '')
    if status_filter:
        complaints = complaints.filter(status=status_filter)
    
    # Filter by PG if user has multiple PGs
    pg_filter = request.GET.get('pg', '')
    if pg_filter:
        complaints = complaints.filter(pg_id=pg_filter)
    
    # Get user's PGs for filter dropdown
    user_pgs = Complaint.objects.filter(user=request.user).values_list('pg__id', 'pg__name').distinct()
    
    # Add public comment count to each complaint
    complaints_list = []
    for complaint in complaints:
        # Count public comments (non-internal)
        complaint.public_comment_count = complaint.comments.filter(is_internal=False).count()
        complaints_list.append(complaint)
    
    context = {
        'complaints': complaints_list,
        'status_choices': Complaint.STATUS_CHOICES,
        'current_status': status_filter,
        'user_pgs': user_pgs,
        'current_pg': pg_filter,
    }
    
    return render(request, 'accounts/complaints/my_complaints.html', context)


@login_required
def create_complaint(request):
    """
    Create a new complaint - only if user has active booking (without leaving date or leaving date in future)
    """
    from django.utils import timezone
    from datetime import date
    
    # Check if user has any active booking (approved and not left)
    # Filter: status=approved AND (no leaving_date OR leaving_date >= today)
    today = date.today()
    active_bookings = Booking.objects.filter(
        user=request.user,
        status='approved'
    ).filter(
        Q(leaving_date__isnull=True) | Q(leaving_date__gte=today)
    ).select_related('pg', 'room')
    
    if not active_bookings.exists():
        messages.error(request, 'You must have an active booking to raise a complaint.')
        # Check if request came from dashboard modal
        if request.META.get('HTTP_REFERER') and 'dashboard' in request.META.get('HTTP_REFERER'):
            return redirect('dashboard')
        return redirect('my_complaints')
    
    if request.method == 'POST':
        # Get form data
        booking_id = request.POST.get('booking')
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        category = request.POST.get('category', 'other')
        priority = request.POST.get('priority', 'medium')
        
        # Auto-select booking if only one active booking and no booking_id provided
        if not booking_id and active_bookings.count() == 1:
            booking_id = active_bookings.first().id
        
        # Validate
        if not title or not description:
            messages.error(request, 'Title and description are required.')
            # Check if request came from dashboard modal
            if request.META.get('HTTP_REFERER') and 'dashboard' in request.META.get('HTTP_REFERER'):
                return redirect('dashboard')
            return redirect('create_complaint')
        
        # Get booking and verify it belongs to user and is still active
        try:
            booking = active_bookings.get(id=booking_id)
        except Booking.DoesNotExist:
            messages.error(request, 'Invalid booking selected.')
            # Check if request came from dashboard modal
            if request.META.get('HTTP_REFERER') and 'dashboard' in request.META.get('HTTP_REFERER'):
                return redirect('dashboard')
            return redirect('create_complaint')
        
        # Create complaint
        complaint = Complaint.objects.create(
            user=request.user,
            pg=booking.pg,
            booking=booking,
            title=title,
            description=description,
            category=category,
            priority=priority,
            status=Complaint.OPEN
        )
        
        messages.success(request, f'Complaint "{complaint.title}" has been submitted successfully.')
        # Check if request came from dashboard modal
        if request.META.get('HTTP_REFERER') and 'dashboard' in request.META.get('HTTP_REFERER'):
            return redirect('dashboard')
        return redirect('complaint_detail', complaint_id=complaint.id)
    
    # Check if user has only one active booking
    single_booking = active_bookings.count() == 1
    
    context = {
        'active_bookings': active_bookings,
        'single_booking': single_booking,
        'category_choices': Complaint.CATEGORY_CHOICES,
        'priority_choices': Complaint.PRIORITY_CHOICES,
    }
    
    return render(request, 'accounts/complaints/create_complaint.html', context)


@login_required
def complaint_detail(request, complaint_id):
    """
    View details of a specific complaint
    User can only view their own complaints
    """
    complaint = get_object_or_404(
        Complaint.objects.select_related('pg', 'booking', 'user', 'resolved_by'),
        id=complaint_id,
        user=request.user
    )
    
    # Get all non-internal comments
    comments = complaint.comments.filter(is_internal=False).select_related('user')
    
    context = {
        'complaint': complaint,
        'comments': comments,
    }
    
    return render(request, 'accounts/complaints/complaint_detail.html', context)
