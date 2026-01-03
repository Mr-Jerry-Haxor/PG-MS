import base64
import os
import uuid
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib import messages
from django.core.files.base import ContentFile
from django.conf import settings

from .models import AdvertisementSettings, AdvertisementImage, AdvertisementText


def _require_pg_admin(user):
    """Check if user is a PG admin."""
    return hasattr(user, 'profile') and getattr(user.profile, 'is_pg_admin', False) and getattr(user.profile, 'status', 'active') == 'active'


def _admin_pgs(user):
    """Get PGs where user is admin."""
    from pgadmin.models import PG
    return PG.objects.filter(admins__user=user).order_by('name')


def _active_pg(request):
    """Get the currently active PG for the admin."""
    from pgadmin.models import PG
    pgs = _admin_pgs(request.user)
    
    # Check for PG selection in query params
    pg_id = request.GET.get('pg') or request.POST.get('pg')
    if pg_id:
        try:
            pg = pgs.get(id=pg_id)
            return pg
        except PG.DoesNotExist:
            pass
    
    # Default to first PG
    return pgs.first()


def _get_or_create_settings(pg):
    """Get or create advertisement settings for a PG."""
    settings_obj, created = AdvertisementSettings.objects.get_or_create(pg=pg)
    return settings_obj


@login_required
def manage_advertisements(request):
    """Main page for managing PG advertisements."""
    if not _require_pg_admin(request.user):
        messages.error(request, "You must be a PG Admin to access this page.")
        return redirect('dashboard')
    
    pg = _active_pg(request)
    if not pg:
        messages.error(request, "No PG assigned to you.")
        return redirect('dashboard')
    
    # Get or create settings
    ad_settings = _get_or_create_settings(pg)
    
    # Get all images and texts
    images = AdvertisementImage.objects.filter(pg=pg).order_by('order', 'created_at')
    texts = AdvertisementText.objects.filter(pg=pg).order_by('order', 'created_at')
    
    context = {
        'pg': pg,
        'pgs': list(_admin_pgs(request.user)),
        'ad_settings': ad_settings,
        'images': images,
        'texts': texts,
    }
    
    return render(request, 'advertisements/manage_advertisements.html', context)


@login_required
@require_POST
def upload_image(request):
    """Handle cropped image upload via AJAX."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        # Get the base64 image data
        image_data = request.POST.get('image_data')
        title = request.POST.get('title', '')
        description = request.POST.get('description', '')
        
        if not image_data:
            return JsonResponse({'error': 'No image data provided'}, status=400)
        
        # Parse base64 data
        if 'base64,' in image_data:
            format_str, imgstr = image_data.split('base64,')
            ext = 'png'  # Default to PNG for cropped images
            if 'jpeg' in format_str or 'jpg' in format_str:
                ext = 'jpg'
            elif 'webp' in format_str:
                ext = 'webp'
        else:
            return JsonResponse({'error': 'Invalid image format'}, status=400)
        
        # Decode and save
        image_bytes = base64.b64decode(imgstr)
        
        # Generate filename
        pg_name = pg.name.replace(' ', '_')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = uuid.uuid4().hex[:8]
        filename = f"{pg_name}_Advertisement_{timestamp}_{unique_id}.{ext}"
        
        # Get the next order number
        max_order = AdvertisementImage.objects.filter(pg=pg).aggregate(
            models_max=models.Max('order')
        )['models_max'] or 0
        
        # Create the image record
        ad_image = AdvertisementImage(
            pg=pg,
            title=title,
            description=description,
            order=max_order + 1,
            is_active=True
        )
        ad_image.image.save(filename, ContentFile(image_bytes), save=True)
        
        return JsonResponse({
            'success': True,
            'image_id': ad_image.id,
            'image_url': ad_image.image.url,
            'title': ad_image.title,
            'order': ad_image.order,
            'message': 'Image uploaded successfully'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# Need to import models for Max
from django.db import models


@login_required
@require_POST
def delete_image(request, image_id):
    """Delete an advertisement image."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        image = get_object_or_404(AdvertisementImage, id=image_id, pg=pg)
        image.delete()
        return JsonResponse({'success': True, 'message': 'Image deleted successfully'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def toggle_image(request, image_id):
    """Toggle an image's active status."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        image = get_object_or_404(AdvertisementImage, id=image_id, pg=pg)
        image.is_active = not image.is_active
        image.save(update_fields=['is_active'])
        return JsonResponse({
            'success': True,
            'is_active': image.is_active,
            'message': f"Image {'enabled' if image.is_active else 'disabled'}"
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def reorder_images(request):
    """Update the order of images."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        import json
        order_data = json.loads(request.body)
        
        for item in order_data.get('order', []):
            image_id = item.get('id')
            new_order = item.get('order')
            if image_id and new_order is not None:
                AdvertisementImage.objects.filter(id=image_id, pg=pg).update(order=new_order)
        
        return JsonResponse({'success': True, 'message': 'Order updated successfully'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def save_text(request):
    """Add or update advertisement text."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        text_id = request.POST.get('text_id')
        text_content = request.POST.get('text', '').strip()
        text_color = request.POST.get('text_color', '#ffffff')
        background_color = request.POST.get('background_color', '#1e3a5f')
        
        if not text_content:
            return JsonResponse({'error': 'Text content is required'}, status=400)
        
        if text_id:
            # Update existing
            ad_text = get_object_or_404(AdvertisementText, id=text_id, pg=pg)
            ad_text.text = text_content
            ad_text.text_color = text_color
            ad_text.background_color = background_color
            ad_text.save()
            message = 'Text updated successfully'
        else:
            # Create new
            max_order = AdvertisementText.objects.filter(pg=pg).aggregate(
                models_max=models.Max('order')
            )['models_max'] or 0
            
            ad_text = AdvertisementText.objects.create(
                pg=pg,
                text=text_content,
                text_color=text_color,
                background_color=background_color,
                order=max_order + 1,
                is_active=True
            )
            message = 'Text added successfully'
        
        return JsonResponse({
            'success': True,
            'text_id': ad_text.id,
            'text': ad_text.text,
            'text_color': ad_text.text_color,
            'background_color': ad_text.background_color,
            'order': ad_text.order,
            'message': message
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def delete_text(request, text_id):
    """Delete an advertisement text."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        ad_text = get_object_or_404(AdvertisementText, id=text_id, pg=pg)
        ad_text.delete()
        return JsonResponse({'success': True, 'message': 'Text deleted successfully'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def toggle_text(request, text_id):
    """Toggle a text's active status."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        ad_text = get_object_or_404(AdvertisementText, id=text_id, pg=pg)
        ad_text.is_active = not ad_text.is_active
        ad_text.save(update_fields=['is_active'])
        return JsonResponse({
            'success': True,
            'is_active': ad_text.is_active,
            'message': f"Text {'enabled' if ad_text.is_active else 'disabled'}"
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def update_settings(request):
    """Update advertisement settings."""
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No PG assigned'}, status=400)
    
    try:
        ad_settings = _get_or_create_settings(pg)
        
        # Update settings from POST data
        carousel_enabled = request.POST.get('carousel_enabled')
        text_enabled = request.POST.get('text_enabled')
        carousel_interval = request.POST.get('carousel_interval')
        text_scroll_speed = request.POST.get('text_scroll_speed')
        
        if carousel_enabled is not None:
            ad_settings.carousel_enabled = carousel_enabled == 'true'
        if text_enabled is not None:
            ad_settings.text_enabled = text_enabled == 'true'
        if carousel_interval:
            ad_settings.carousel_interval = max(1000, int(carousel_interval))
        if text_scroll_speed:
            ad_settings.text_scroll_speed = max(10, min(200, int(text_scroll_speed)))
        
        ad_settings.save()
        
        return JsonResponse({
            'success': True,
            'carousel_enabled': ad_settings.carousel_enabled,
            'text_enabled': ad_settings.text_enabled,
            'carousel_interval': ad_settings.carousel_interval,
            'text_scroll_speed': ad_settings.text_scroll_speed,
            'message': 'Settings updated successfully'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# API endpoint for dashboard to fetch advertisements
@login_required
@require_GET
def get_advertisements(request, pg_id):
    """Get active advertisements for a PG (used by dashboard)."""
    from pgadmin.models import PG
    
    try:
        pg = get_object_or_404(PG, id=pg_id)
        
        # Get settings
        try:
            ad_settings = pg.advertisement_settings
        except AdvertisementSettings.DoesNotExist:
            ad_settings = None
        
        # Get active images if carousel is enabled
        images = []
        if ad_settings is None or ad_settings.carousel_enabled:
            for img in AdvertisementImage.objects.filter(pg=pg, is_active=True).order_by('order'):
                images.append({
                    'id': img.id,
                    'url': img.image.url,
                    'title': img.title,
                    'description': img.description,
                })
        
        # Get active texts if text is enabled
        texts = []
        if ad_settings is None or ad_settings.text_enabled:
            for txt in AdvertisementText.objects.filter(pg=pg, is_active=True).order_by('order'):
                texts.append({
                    'id': txt.id,
                    'text': txt.text,
                    'text_color': txt.text_color,
                    'background_color': txt.background_color,
                })
        
        return JsonResponse({
            'success': True,
            'carousel_enabled': ad_settings.carousel_enabled if ad_settings else True,
            'text_enabled': ad_settings.text_enabled if ad_settings else True,
            'carousel_interval': ad_settings.carousel_interval if ad_settings else 5000,
            'text_scroll_speed': ad_settings.text_scroll_speed if ad_settings else 50,
            'images': images,
            'texts': texts,
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
