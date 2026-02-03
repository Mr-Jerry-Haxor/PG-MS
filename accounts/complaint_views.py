"""
User-facing complaint views
Users can view their complaints, create new ones, and view details
"""
import json
import logging
import os
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.core.files.storage import default_storage
from django.conf import settings
from pgadmin.models import Complaint, ComplaintComment, ComplaintMedia
from bookings.models import Booking

_logger = logging.getLogger(__name__)


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
        
        # Get uploaded media info (JSON string of uploaded file data)
        media_data = request.POST.get('media_data', '[]')
        try:
            media_list = json.loads(media_data)
        except json.JSONDecodeError:
            media_list = []
        
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
        
        # Link uploaded media files to the complaint
        for media_item in media_list:
            if media_item.get('file_url'):
                ComplaintMedia.objects.create(
                    complaint=complaint,
                    media_type=media_item.get('media_type', 'image'),
                    file_url=media_item['file_url'],
                    file_name=media_item.get('file_name', ''),
                    file_size=media_item.get('file_size'),
                    thumbnail_url=media_item.get('thumbnail_url', '')
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
    
    # Get all media files attached to complaint
    media_files = complaint.media_files.all()
    
    context = {
        'complaint': complaint,
        'comments': comments,
        'media_files': media_files,
    }
    
    return render(request, 'accounts/complaints/complaint_detail.html', context)


@login_required
@require_POST
def upload_complaint_media(request):
    """
    AJAX endpoint to upload a single media file to local media folder for complaints.
    Returns the file URL immediately so user can see it before final submission.
    """
    if not request.FILES.get('file'):
        return JsonResponse({'ok': False, 'error': 'No file provided.'}, status=400)
    
    file = request.FILES['file']
    file_name = file.name
    file_size = file.size
    
    # Determine media type
    content_type = file.content_type or ''
    if content_type.startswith('image/'):
        media_type = 'image'
    elif content_type.startswith('video/'):
        media_type = 'video'
    else:
        return JsonResponse({'ok': False, 'error': 'Only images and videos are allowed.'}, status=400)
    
    # File size limits: 10MB for images, 50MB for videos
    max_size_image = 10 * 1024 * 1024  # 10MB
    max_size_video = 50 * 1024 * 1024  # 50MB
    
    if media_type == 'image' and file_size > max_size_image:
        return JsonResponse({'ok': False, 'error': 'Image size must be less than 10MB.'}, status=400)
    if media_type == 'video' and file_size > max_size_video:
        return JsonResponse({'ok': False, 'error': 'Video size must be less than 50MB.'}, status=400)
    
    # Upload to local media folder
    try:
        # Create a folder structure: complaints/user_id/timestamp
        import uuid
        from datetime import datetime
        
        user_folder = f"complaints/{request.user.id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        
        # Generate safe filename
        name, ext = os.path.splitext(file_name)
        safe_filename = f"{timestamp}_{unique_id}{ext}"
        file_path = f"{user_folder}/{safe_filename}"
        
        # Save the file
        file_path = default_storage.save(file_path, file)
        
        # Get the full URL for the file
        file_url = default_storage.url(file_path)
        
        # Generate thumbnail URL for images
        thumbnail_url = ''
        if media_type == 'image':
            # For local files, use the same URL (browser will handle image display)
            thumbnail_url = file_url
        
        return JsonResponse({
            'ok': True,
            'file_url': file_url,
            'file_path': file_path,  # Store the path for deletion later
            'file_name': file_name,
            'file_size': file_size,
            'media_type': media_type,
            'thumbnail_url': thumbnail_url,
        })
        
    except Exception as e:
        _logger.error(f"Error uploading complaint media: {e}")
        return JsonResponse({'ok': False, 'error': 'Upload failed. Please try again.'}, status=500)


@login_required
@require_POST
def delete_complaint_media(request):
    """
    AJAX endpoint to delete an uploaded media file from local media folder (before complaint submission).
    This is called when user removes a file from the upload list before submitting the complaint.
    """
    try:
        data = json.loads(request.body)
        file_path = data.get('file_path')
        
        if not file_path:
            return JsonResponse({'ok': False, 'error': 'No file path provided.'}, status=400)
        
        # Delete from local storage
        try:
            if default_storage.exists(file_path):
                default_storage.delete(file_path)
        except Exception as e:
            _logger.warning(f"Failed to delete file from storage: {e}")
            # Continue anyway - file might not exist or already deleted
        
        return JsonResponse({'ok': True, 'message': 'File deleted.'})
        
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON data.'}, status=400)
    except Exception as e:
        _logger.error(f"Error deleting complaint media: {e}")
        return JsonResponse({'ok': False, 'error': 'Delete failed.'}, status=500)
