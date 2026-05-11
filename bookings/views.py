import io
import calendar as _cal
from urllib.parse import urlencode
from datetime import timedelta, date
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.db.models import Q

from accounts.models import Profile
from core.audit import log
from core.drive import drive_upload, drive_delete
from core.models import Notification
from core.push_notifications import send_push_to_users
from django.core.mail import send_mail
from django.db import IntegrityError
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.http import JsonResponse, HttpResponseBadRequest
from pgadmin.models import PG, PGAdmin

from .application_forms import ResidentApplicationForm
from .forms import AadhaarForm, BookingRequestForm
from .models import Room, RoomShareStatus, Booking, RoomSwap


def _normalize_payment_day(payment_date, anchor_date):
    """
    Given a raw payment_date (potentially months away from anchor_date) and
    an anchor_date (booking joining/admission date), return a normalized date
    with the same day-of-month placed in the first eligible month on-or-after
    anchor_date where the result falls within 31 days of anchor_date.

    Example: anchor=2025-11-01, payment_date=2026-01-05 → target_day=5
             Nov 5 - Nov 1 = 4 days ✓  →  returns 2025-11-05
    Falls back to anchor_date if no valid placement found in 3 months.
    """
    if not payment_date or not anchor_date:
        return anchor_date or payment_date
    target_day = payment_date.day
    year, month = anchor_date.year, anchor_date.month
    for _ in range(3):  # try current month, next month, month after
        last = _cal.monthrange(year, month)[1]
        day = min(target_day, last)
        candidate = date(year, month, day)
        diff = (candidate - anchor_date).days
        if 0 <= diff <= 31:
            return candidate
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return anchor_date


def _next_payment_due_date(reference_date: date, payment_day: int) -> date:
    """Return the next payment due date strictly after reference_date."""
    day = max(1, min(31, int(payment_day or reference_date.day)))

    year = reference_date.year
    month = reference_date.month
    last_day = _cal.monthrange(year, month)[1]
    candidate = date(year, month, min(day, last_day))

    if candidate <= reference_date:
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
        last_day = _cal.monthrange(year, month)[1]
        candidate = date(year, month, min(day, last_day))

    return candidate


def _recommended_leave_date(reference_date: date, payment_day: int):
    """
    Compute both next due date and the suggested leave date.

    Business rule:
    - Suggested leave date is always one day before the next payment due date.
    - For payment day=1, this naturally becomes previous month's last day
      (28/29/30/31 depending on month length).
    """
    due_date = _next_payment_due_date(reference_date, payment_day)
    day_one_adjustment = int(payment_day or 0) == 1
    leave_date = due_date - timedelta(days=1)

    # Ensure the suggested leave date is always in the future.
    while leave_date <= reference_date:
        due_date = _next_payment_due_date(due_date, payment_day)
        leave_date = due_date - timedelta(days=1)

    return due_date, leave_date, day_one_adjustment


def _pg_by_slug_or_404(slug: str):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(PG, slug=slug)


def _pg_admin_users(pg):
    """Return unique user objects for all admins of the PG."""
    users_by_id = {}
    for admin_profile in pg.admins.select_related('user').all():
        admin_user = getattr(admin_profile, 'user', None)
        if admin_user and getattr(admin_user, 'id', None):
            users_by_id[admin_user.id] = admin_user
    return list(users_by_id.values())


def _leave_admin_path_and_payload(pg_id, booking):
    """Build leave-page deep link and push payload with PG+user filters."""
    user_obj = getattr(booking, 'user', None)
    search_value = (
        (getattr(user_obj, 'email', '') or '').strip()
        or ((user_obj.get_full_name() or '').strip() if user_obj and hasattr(user_obj, 'get_full_name') else '')
    )
    params = {
        'pg': pg_id,
        'booking_id': booking.id,
        'user_id': getattr(booking, 'user_id', ''),
    }
    if search_value:
        params['search'] = search_value

    path = f"{reverse('pg_leaving_requests_enhanced')}?{urlencode(params)}"

    payload = {
        'booking_id': booking.id,
        'pg_id': pg_id,
        'user_id': getattr(booking, 'user_id', ''),
    }
    if search_value:
        payload['search'] = search_value
    if getattr(user_obj, 'email', None):
        payload['user_email'] = user_obj.email

    return path, payload

@login_required
def pg_quick_booking(request, pgslug):
    """
    Unified quick booking view supporting three booking types:
    1. Day-wise booking - Short-term stay without room assignment (admin assigns later)
    2. Book now - Traditional immediate booking with room selection
    3. Book for future - Future-dated booking showing vacant and vacant_from rooms
    """
    from .application_forms import ResidentApplicationForm
    from .models import ResidentApplication, ApplicationStatusHistory
    pg = _pg_by_slug_or_404(pgslug)
    
    # Check if user has an active booking (PENDING/APPROVED status and either no leaving date or leaving date in future)
    # Allow booking if user has left (leaving_confirmed_date is set and in the past)
    today = date.today()
    has_active = Booking.objects.filter(
        user=request.user, 
        room__pg=pg, 
        status__in=[Booking.PENDING, Booking.APPROVED]
    ).filter(
        Q(leaving_confirmed_date__isnull=True) | Q(leaving_confirmed_date__gt=today)
    ).exists()

    context = {'pg': pg, 'has_active': has_active}

    if request.method == 'GET':
        # If user already has an active booking in this PG, prevent new booking
        if has_active:
            messages.warning(request, "You already have one booking in this PG; you can't book another room.")
            return redirect('dashboard')
        # Prefill application form basics
        initial = {
            'name': f"{request.user.first_name} {request.user.last_name}".strip(),
            'phone': getattr(getattr(request.user, 'profile', None), 'phone', ''),
            'email': request.user.email,
        }
        context['form'] = ResidentApplicationForm(initial=initial)
        # Use the new template with modal for 3 booking types
        return render(request, 'bookings/quick_booking_new.html', context)

    # POST: Determine which booking type was submitted
    booking_type = request.POST.get('booking_type', '').strip().lower()
    
    if booking_type == 'daywise':
        return handle_daywise_booking(request, pg, has_active)
    elif booking_type == 'future':
        return handle_future_booking(request, pg, has_active)
    elif booking_type == 'booknow':
        return handle_booknow_booking(request, pg, has_active, context)
    else:
        messages.error(request, "Invalid booking type selected.")
        return redirect('pg_quick_booking', pgslug=pg.slug)


def handle_daywise_booking(request, pg, has_active):
    """
    Handle day-wise booking submission:
    - No room assignment (admin assigns later)
    - Creates Booking with booking_type='daywise' and status='pending'
    - Creates ResidentApplication with guest details
    - Uploads selfie/aadhaar to Google Drive and stores URLs
    """
    from .models import Booking, ResidentApplication
    import base64
    errors = []
    
    # Day-wise bookings don't conflict with regular bookings
    # They're short-term stays, so users can have day-wise booking even with active regular booking
    
    # Extract form data
    name = request.POST.get('daywise_name', '').strip()
    mobile = request.POST.get('daywise_mobile', '').strip()
    emergency_contact = request.POST.get('daywise_emergency', '').strip()
    start_date_raw = request.POST.get('daywise_start_date', '').strip()
    end_date_raw = request.POST.get('daywise_end_date', '').strip()
    start_time_raw = request.POST.get('daywise_start_time', '').strip()
    end_time_raw = request.POST.get('daywise_end_time', '').strip()
    purpose = request.POST.get('daywise_purpose', '').strip()
    selfie_data = request.POST.get('daywise_selfie_data', '').strip()
    
    # Validate required fields
    if not name:
        errors.append("Name is required for day-wise booking.")
    if not mobile:
        errors.append("Mobile number is required.")
    if not emergency_contact:
        errors.append("Emergency contact is required.")
    if not start_date_raw:
        errors.append("Start date is required.")
    if not end_date_raw:
        errors.append("End date is required.")
    if not purpose:
        errors.append("Purpose of stay is required.")
    if not selfie_data:
        errors.append("Selfie capture is required.")
    
    # Validate dates
    start_date = parse_date(start_date_raw) if start_date_raw else None
    end_date = parse_date(end_date_raw) if end_date_raw else None
    if not start_date:
        errors.append("Invalid start date.")
    if not end_date:
        errors.append("Invalid end date.")
    if start_date and end_date and end_date < start_date:
        errors.append("End date must be on or after start date.")
    
    # Validate time formats
    from datetime import time as dt_time
    start_time = None
    end_time = None
    if start_time_raw:
        try:
            h, m = map(int, start_time_raw.split(':'))
            # Enforce hour-only times (minutes must be 0)
            if m != 0:
                errors.append("Start time must be on the hour (minutes must be 00).")
            start_time = dt_time(h, m)
        except:
            errors.append("Invalid start time format.")
    if end_time_raw:
        try:
            h, m = map(int, end_time_raw.split(':'))
            # Enforce hour-only times (minutes must be 0)
            if m != 0:
                errors.append("End time must be on the hour (minutes must be 00).")
            end_time = dt_time(h, m)
        except:
            errors.append("Invalid end time format.")
    
    # Validate Aadhaar documents (at least one required)
    aadhaar_doc1 = request.FILES.get('daywise_aadhaar1')
    aadhaar_doc2 = request.FILES.get('daywise_aadhaar2')
    if not aadhaar_doc1:
        errors.append("At least one Aadhaar document is required.")
    
    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect('pg_quick_booking', pgslug=pg.slug)
    
    try:
        with transaction.atomic():
            # Use the actual logged-in user for day-wise bookings
            # This allows users to see their day-wise bookings in their dashboard
            booking_user = request.user if request.user.is_authenticated else None
            
            if not booking_user:
                messages.error(request, "You must be logged in to create a booking.")
                return redirect('pg_quick_booking', pgslug=pg.slug)
            
            # Get first room for temporary assignment (admin will reassign)
            temp_room = pg.rooms.first()
            if not temp_room:
                messages.error(request, "No rooms available in this PG.")
                return redirect('pg_quick_booking', pgslug=pg.slug)
            
            # Create Booking with booking_type='daywise'
            booking = Booking.objects.create(
                user=booking_user,  # Use actual user instead of system_user
                room=temp_room,
                pg=pg,
                share_no=1,  # Temporary, will be set during approval
                booking_type=Booking.DAYWISE,
                status=Booking.PENDING,
                joining_date=start_date,  # start_date → joining_date
                leaving_date=end_date,    # end_date → leaving_date
                start_time=start_time,
                end_time=end_time,
                purpose=purpose,
                payment_received=False,
            )
            
            # Process and upload selfie to Google Drive
            selfie_url = ''
            if selfie_data and selfie_data.startswith('data:image'):
                try:
                    format_part, img_str = selfie_data.split(';base64,')
                    ext = format_part.split('/')[-1]
                    img_data = base64.b64decode(img_str)
                    filename = f'daywise_selfie_{booking.id}.{ext}'
                    
                    # Upload to Google Drive using core.drive.drive_upload
                    try:
                        from io import BytesIO as _BytesIO
                        from core.drive import drive_upload as _drive_upload
                        folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', '') or None
                        buf = _BytesIO(img_data)
                        up = _drive_upload(buf, filename, folder)
                        selfie_url = up[1] if up else ''
                    except Exception as _e:
                        print(f"Selfie upload failed (drive): {_e}")
                except Exception as e:
                    # Non-fatal but log
                    print(f"Selfie upload failed: {e}")
            
            # Upload Aadhaar documents to Google Drive
            aadhaar_url_1 = ''
            aadhaar_url_2 = ''
            if aadhaar_doc1:
                try:
                    from io import BytesIO as _BytesIO
                    from core.drive import drive_upload as _drive_upload
                    folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '') or None
                    buf1 = _BytesIO(aadhaar_doc1.read())
                    up1 = _drive_upload(buf1, f'daywise_aadhaar1_{booking.id}_{aadhaar_doc1.name}', folder)
                    aadhaar_url_1 = up1[1] if up1 else ''
                except Exception as e:
                    print(f"Aadhaar doc 1 upload failed (drive): {e}")
            
            if aadhaar_doc2:
                try:
                    from io import BytesIO as _BytesIO
                    from core.drive import drive_upload as _drive_upload
                    folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '') or None
                    buf2 = _BytesIO(aadhaar_doc2.read())
                    up2 = _drive_upload(buf2, f'daywise_aadhaar2_{booking.id}_{aadhaar_doc2.name}', folder)
                    aadhaar_url_2 = up2[1] if up2 else ''
                except Exception as e:
                    print(f"Aadhaar doc 2 upload failed (drive): {e}")
            
            # Create ResidentApplication linked to the booking user
            ResidentApplication.objects.create(
                user=booking_user,
                booking=booking,
                pg=pg,
                room=temp_room,
                status=ResidentApplication.SUBMITTED,
                name=name,
                phone=mobile,
                emergency_contact=emergency_contact,
                email=(booking_user.email or f'daywise_{booking.id}@guest.local'),
                selfie_url=selfie_url,
                aadhaar_file_url=aadhaar_url_1,
                aadhaar_file_url_2=aadhaar_url_2,
            )
            
            # Notify PG admins
            try:
                admin_profiles = list(pg.admins.select_related('user').all())
                pending_path = f"{reverse('pg_bookings_pending')}?{urlencode({'pg': pg.id})}"
                admin_url = request.build_absolute_uri(pending_path)
                for ap in admin_profiles:
                    Notification.objects.create(
                        user=ap.user,
                        title="Day-Wise Booking Request",
                        message=(
                            f"New day-wise booking request from {name} ({mobile}) "
                            f"for {start_date} to {end_date}. Review and assign room: {admin_url}"
                        ),
                    )

                send_push_to_users(
                    [ap.user for ap in admin_profiles],
                    title="Day-Wise Booking Request",
                    body=f"{name} requested {start_date} to {end_date}.",
                    url=pending_path,
                    extra_data={'type': 'booking_request', 'booking_type': 'daywise', 'pg_id': pg.id},
                )
                # Email notification
                admin_emails = [ap.user.email for ap in admin_profiles if getattr(ap.user, 'email', None)]
                if admin_emails:
                    send_mail(
                        subject="PG-MS: Day-Wise Booking Request",
                        message=(
                            f"A new day-wise booking request has been submitted.\n\n"
                            f"PG: {pg.name}\n"
                            f"Guest: {name}\n"
                            f"Mobile: {mobile}\n"
                            f"Period: {start_date} to {end_date}\n"
                            f"Purpose: {purpose}\n\n"
                            f"Review and assign room: {admin_url}\n"
                        ),
                        from_email=None,
                        recipient_list=admin_emails,
                        fail_silently=True,
                    )
            except Exception:
                pass
            
            messages.success(request, "Day-wise booking request submitted successfully. You'll be notified once a room is assigned.")
            return redirect('dashboard')
            
    except Exception as ex:
        messages.error(request, f"Failed to create day-wise booking: {str(ex)}")
        return redirect('pg_quick_booking', pgslug=pg.slug)


def handle_future_booking(request, pg, has_active):
    """
    Handle future booking submission:
    - Shows rooms with VACANT or VACANT_FROM status
    - Requires joining date >= vacant_from date
    - Creates regular Booking with PENDING status
    """
    from .application_forms import ResidentApplicationForm
    from .models import ResidentApplication, ApplicationStatusHistory
    
    errors = []
    if has_active:
        errors.append("You can't book another room in the same PG.")
    
    # Extract form data
    room_id = request.POST.get('future_room_id')
    share_no_raw = request.POST.get('future_share_no')
    joining_raw = request.POST.get('future_joining_date', '')
    name = request.POST.get('future_name', '').strip()
    phone = request.POST.get('future_phone', '').strip()
    
    # Validate basic fields
    if not name:
        errors.append("Name is required.")
    if not phone:
        errors.append("Phone number is required.")
    
    room = None
    rs = None
    try:
        room = Room.objects.get(pk=room_id, pg=pg)
    except Exception:
        errors.append('Invalid room selection.')
    
    try:
        share_no = int(share_no_raw) if share_no_raw is not None else None
    except Exception:
        share_no = None
        errors.append('Invalid share selection.')
    
    if room and share_no is not None:
        rs = RoomShareStatus.objects.filter(room=room, share_no=share_no).first()
        if not rs:
            errors.append('Share not found for room.')
    
    # Validate joining date
    today = timezone.now().date()
    if not joining_raw:
        errors.append('Joining date is required.')
    joining_date = parse_date(joining_raw) if joining_raw else None
    if joining_date is None:
        errors.append('Enter a valid joining date.')
    else:
        # For future bookings, joining date must be in the future (can be today or later)
        if joining_date < today:
            errors.append('Joining date must be today or in the future for future bookings.')
        # Optional: Set a maximum future date (e.g., 60 days from today)
        from datetime import timedelta
        max_future_date = today + timedelta(days=60)
        if joining_date > max_future_date:
            errors.append(f'Joining date cannot be more than 60 days in the future (max: {max_future_date}).')
    
    # Validate share availability for the joining date
    if rs and joining_date:
        can_book_on_date = False
        if rs.status == RoomShareStatus.VACANT:
            can_book_on_date = True
        elif rs.status == RoomShareStatus.VACANT_FROM:
            # Joining date must be >= vacant_from date
            can_book_on_date = (not rs.vacant_from) or (rs.vacant_from <= joining_date)
        
        if not can_book_on_date:
            errors.append('Selected share is not available for the chosen joining date.')
        
        # Check for pending future swaps targeting this bed
        if room and share_no:
            has_future_swap = RoomSwap.objects.filter(
                to_room=room,
                to_share_no=share_no,
                is_future_swap=True,
                status__in=[RoomSwap.PENDING, RoomSwap.APPROVED]
            ).exists()
            if has_future_swap:
                errors.append('This bed has a pending room swap and is not available for booking.')
    
    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect('pg_quick_booking', pgslug=pg.slug)
    
    try:
        with transaction.atomic():
            # Create booking with PENDING status
            booking_obj = Booking.objects.create(
                user=request.user,
                room=room,
                pg=pg,
                share_no=share_no,
                status=Booking.PENDING,
                joining_date=joining_date,
                payment_date=joining_date,  # Default to joining date
            )
            
            # Reserve share (status will be RESERVED, not OCCUPIED until joining date)
            if rs.status in [RoomShareStatus.VACANT, RoomShareStatus.VACANT_FROM]:
                rs.status = RoomShareStatus.RESERVED
                rs.save(update_fields=['status'])
            
            # Update user profile phone if provided
            try:
                from accounts.models import Profile as _Profile
                if phone:
                    prof, _ = _Profile.objects.get_or_create(user=request.user)
                    if prof.phone != phone:
                        prof.phone = phone
                        prof.save(update_fields=['phone'])
            except Exception:
                pass
            
            # Notify admins
            try:
                pending_path = f"{reverse('pg_bookings_pending')}?{urlencode({'pg': pg.id})}"
                admin_url = request.build_absolute_uri(pending_path)
                admin_profiles = list(pg.admins.select_related('user').all())
                for ap in admin_profiles:
                    Notification.objects.create(
                        user=ap.user,
                        title="Future Booking Request",
                        message=(
                            f"{name} ({phone}) submitted a future booking request for Room {room.room_no} Share {share_no}, "
                            f"joining on {joining_date}. Review: {admin_url}"
                        ),
                    )
                send_push_to_users(
                    [ap.user for ap in admin_profiles],
                    title="Future Booking Request",
                    body=f"{name} requested Room {room.room_no}, Bed {share_no} joining {joining_date}.",
                    url=pending_path,
                    extra_data={'type': 'booking_request', 'booking_type': 'future', 'pg_id': pg.id},
                )
                admin_emails = [ap.user.email for ap in admin_profiles if getattr(ap.user, 'email', None)]
                if admin_emails:
                    send_mail(
                        subject="PG-MS: Future Booking Request",
                        message=(
                            f"A future booking request has been submitted.\n\n"
                            f"PG: {pg.name}\n"
                            f"Room: {room.room_no} | Share: {share_no}\n"
                            f"Guest: {name}\n"
                            f"Phone: {phone}\n"
                            f"Joining Date: {joining_date}\n\n"
                            f"Review and approve: {admin_url}\n"
                        ),
                        from_email=None,
                        recipient_list=admin_emails,
                        fail_silently=True,
                    )
            except Exception:
                pass
            
            messages.success(request, f'Future booking request submitted for {joining_date}. You will receive further instructions after approval.')
            return redirect('dashboard')
            
    except IntegrityError:
        messages.error(request, "You already have an active booking in this PG.")
        return redirect('pg_quick_booking', pgslug=pg.slug)
    except Exception as ex:
        messages.error(request, f'Failed to create future booking: {str(ex)}')
        return redirect('pg_quick_booking', pgslug=pg.slug)


def handle_booknow_booking(request, pg, has_active, context):
    """
    Handle traditional 'book now' booking submission (existing flow)
    - Same logic as previous pg_quick_booking POST handling
    - Enforces ±7 days joining date window from today
    """
    from .application_forms import ResidentApplicationForm
    from .models import ResidentApplication, ApplicationStatusHistory
    from datetime import timedelta
    
    # POST: process booking + application in one go; show errors inline on same page
    errors = []
    if has_active:
        errors.append("You can't book another room in the same PG.")

    room_id = request.POST.get('room_id')
    share_no_raw = request.POST.get('share_no')
    joining_raw = request.POST.get('joining_date', '')
    room = None
    rs = None
    try:
        room = Room.objects.get(pk=room_id, pg=pg)
    except Exception:
        errors.append('Invalid room selection.')
    try:
        share_no = int(share_no_raw) if share_no_raw is not None else None
    except Exception:
        share_no = None
        errors.append('Invalid share selection.')
    if room and share_no is not None:
        rs = RoomShareStatus.objects.filter(room=room, share_no=share_no).first()
        if not rs:
            errors.append('Share not found for room.')

    today = timezone.now().date()
    if not joining_raw:
        errors.append('Joining date is required.')
    joining_date = parse_date(joining_raw) if joining_raw else None
    if joining_date is None:
        errors.append('Enter a valid joining date.')
    else:
        # Book Now: joining date validation based on PG settings
        if getattr(pg, 'past_joining_date_allowed', False):
            # Allow past dates (up to 7 years in the past) to 7 days in the future
            min_date = today - timedelta(days=365*7)  # 7 years back
            max_date = today + timedelta(days=7)
            if joining_date < min_date or joining_date > max_date:
                errors.append(f'For Book Now, joining date must be between {min_date} and {max_date}.')
        else:
            # Only allow today to 7 days in the future
            max_date = today + timedelta(days=7)
            if joining_date < today:
                errors.append('Joining date cannot be in the past.')
            elif joining_date > max_date:
                errors.append(f'For Book Now, joining date must be within 7 days from today (by {max_date}).')

    # Prepare application form data (ensure date_of_admission mirrors joining_date)
    data = request.POST.copy()
    if not data.get('date_of_admission'):
        data['date_of_admission'] = joining_date.isoformat() if joining_date else ''
    # Always enforce email to be current user's email (readonly in form)
    data['email'] = request.user.email
    form = ResidentApplicationForm(data, request.FILES)

    # Enforce DOB must be before 2010-01-01
    dob_raw = data.get('dob')
    if dob_raw:
        try:
            dob_val = parse_date(dob_raw)
            if dob_val and dob_val >= timezone.datetime(2010,1,1).date():
                errors.append('Date of Birth must be before the year 2010.')
        except Exception:
            pass

    # Validate share availability
    if rs:
        # Determine whether the share is available at the requested joining_date
        can_book_on_date = False
        if joining_date:
            if rs.status == RoomShareStatus.VACANT:
                can_book_on_date = True
            elif rs.status == RoomShareStatus.VACANT_FROM:
                # If vacant_from is not set or is on/before the joining_date, it's selectable
                can_book_on_date = (not rs.vacant_from) or (rs.vacant_from <= joining_date)

        if not can_book_on_date:
            current = (
                Booking.objects.filter(room=room, share_no=share_no, status=Booking.APPROVED)
                .order_by('-created_at').first()
            )
            # If there's no current booking or leaving date doesn't free it before requested joining_date, reject
            if not current or not current.leaving_date or not (joining_date and joining_date > current.leaving_date):
                errors.append('Selected share is not yet available for the chosen date.')
        
        # Check for pending future swaps targeting this bed
        if room and share_no:
            has_future_swap = RoomSwap.objects.filter(
                to_room=room,
                to_share_no=share_no,
                is_future_swap=True,
                status__in=[RoomSwap.PENDING, RoomSwap.APPROVED]
            ).exists()
            if has_future_swap:
                errors.append('This bed has a pending room swap and is not available for booking.')

    # Validate application form last, accumulate errors
    if not form.is_valid():
        # Collect field errors into errors list (brief)
        for fld, errs in form.errors.items():
            for er in errs:
                errors.append(f"{fld}: {er}")

    # Validate payment day selection (shown as "Final Joining Date" in UI)
    payment_day_raw = request.POST.get('payment_day', '')
    payment_date_selected = None
    if not payment_day_raw:
        errors.append('Final joining date is required. Please select a joining date first.')
    else:
        try:
            payment_date_selected = parse_date(payment_day_raw)
            if not payment_date_selected:
                errors.append('Invalid final joining date format.')
            elif joining_date:
                # Normalize: extract day-of-month and place it within 31 days of joining
                payment_date_selected = _normalize_payment_day(payment_date_selected, joining_date)
        except (ValueError, TypeError):
            errors.append('Invalid final joining date.')

    # Validate declaration checkbox
    decl_agreed = request.POST.get('decl_agreed')
    if decl_agreed != 'on':
        errors.append('You must agree to the declaration.')

    # Enforce mandatory files: selfie and Aadhaar/other must be provided
    selfie_file = request.FILES.get('selfie')
    aadhaar_in_form = form.cleaned_data.get('aadhaar_pdf') if hasattr(form, 'cleaned_data') else None
    if not selfie_file:
        errors.append('Selfie photo is required.')
    if not aadhaar_in_form:
        # Either no files or validation failed; add explicit error
        errors.append('Aadhaar Document 1 is required.')

    if errors:
        context.update({
            'errors': errors,
            'form': form,
            'selected_room_id': room_id,
            'selected_share_no': share_no_raw,
            'joining_date_value': joining_raw,
        })
        return render(request, 'bookings/quick_booking_new.html', context)

    # All validations passed; create booking and application inside a transaction
    try:
        with transaction.atomic():
            # Use the selected payment date (final joining date)
            payment_date_calculated = payment_date_selected if payment_date_selected else joining_date

            booking_obj = Booking.objects.create(
                user=request.user,
                room=room,
                pg=pg,
                share_no=share_no,
                status=Booking.PENDING,
                joining_date=joining_date,
                payment_date=payment_date_calculated,
            )
            # Reserve share when applicable
            if rs.status in [RoomShareStatus.VACANT, RoomShareStatus.VACANT_FROM]:
                rs.status = RoomShareStatus.RESERVED
                rs.save(update_fields=['status'])

            inst = form.save(commit=False)
            inst.user = request.user
            inst.booking = booking_obj
            inst.pg = pg
            inst.room = room
            inst.date_of_admission = joining_date
            # Set all declaration fields to True (user agreed to combined declaration)
            inst.decl_valuables = True
            inst.decl_notice = True
            inst.decl_deposit = True
            inst.decl_truth = True

            # Files handling with two separate Aadhaar fields
            selfie_file = request.FILES.get('selfie')
            aadhaar_file_1 = form.cleaned_data.get('aadhaar_pdf')
            aadhaar_file_2 = form.cleaned_data.get('aadhaar_pdf_2')
            
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            
            # Handle Aadhaar Document 1 (required)
            folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
            if aadhaar_file_1:
                name = (getattr(aadhaar_file_1, 'name', '') or '').lower()
                ctype = getattr(aadhaar_file_1, 'content_type', '') or ''
                is_pdf = ctype == 'application/pdf' or name.endswith('.pdf')
                
                if is_pdf:
                    # Upload PDF
                    up = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                else:
                    # Upload image (front side)
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    ext1 = _pick_ext(name)
                    up1 = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                    
                    # Handle Aadhaar Document 2 (optional - back side)
                    if aadhaar_file_2:
                        name2 = (getattr(aadhaar_file_2, 'name', '') or '').lower()
                        ext2 = _pick_ext(name2)
                        up2 = drive_upload(aadhaar_file_2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2
                    else:
                        inst.aadhaar_file_url_2 = ''


            inst.status = ResidentApplication.SUBMITTED
            inst.save()
            ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Submitted by user')

            # Persist phone number to the user's Profile
            try:
                from accounts.models import Profile as _Profile
                phone_norm = (form.cleaned_data.get('phone') or '').strip()
                if phone_norm:
                    prof, _ = _Profile.objects.get_or_create(user=request.user)
                    if prof.phone != phone_norm:
                        prof.phone = phone_norm
                        prof.save(update_fields=['phone'])
            except Exception:
                # Non-fatal: failure to update phone shouldn't block booking
                pass

            # Notify admins (best-effort)
            try:
                admin_path = f"{reverse('pg_resident_applications')}?{urlencode({'pg': pg.id, 'email': inst.user.email or ''})}"
                admin_url = request.build_absolute_uri(admin_path)
                admin_profiles = list(pg.admins.select_related('user').all())
                for ap in admin_profiles:
                    Notification.objects.create(
                        user=ap.user,
                        title="Resident Application Submitted",
                        message=(
                            f"{inst.user.email} submitted an application for Room {room.room_no} Share {share_no}. "
                            f"Review and confirm: {admin_url}"
                        ),
                    )
                send_push_to_users(
                    [ap.user for ap in admin_profiles],
                    title="Resident Application Submitted",
                    body=f"{inst.user.email} submitted application for Room {room.room_no}, Bed {share_no}.",
                    url=admin_path,
                    extra_data={'type': 'application_submitted', 'application_status': inst.status, 'pg_id': pg.id},
                )
                admin_emails = [ap.user.email for ap in admin_profiles if getattr(ap.user, 'email', None)]
                if admin_emails:
                    send_mail(
                        subject="PG-MS: Resident Application Submitted",
                        message=(
                            f"A resident application was submitted and awaits your confirmation.\n\n"
                            f"PG: {pg.name}\n"
                            f"Room: {room.room_no} | Share: {share_no}\n"
                            f"Applicant: {inst.name or inst.user.get_full_name() or inst.user.email}\n"
                            f"Email: {inst.user.email}\n\n"
                            f"View and confirm here: {admin_url}\n"
                        ),
                        from_email=None,
                        recipient_list=admin_emails,
                        fail_silently=True,
                    )
            except Exception:
                pass
    except IntegrityError:
        errors.append("You already have an active booking in this PG.")
    except Exception as ex:
        errors.append('Failed to create booking/application. Please try again.')

    if errors:
        context.update({
            'errors': errors,
            'form': form,
            'selected_room_id': room_id,
            'selected_share_no': share_no_raw,
            'joining_date_value': joining_raw,
        })
        return render(request, 'bookings/quick_booking_new.html', context)

    messages.success(request, 'Booking request and application submitted.')
    return redirect('dashboard')


    # POST: process booking + application in one go; show errors inline on same page
    errors = []
    if has_active:
        errors.append("You can't book another room in the same PG.")

    room_id = request.POST.get('room_id')
    share_no_raw = request.POST.get('share_no')
    joining_raw = request.POST.get('joining_date', '')
    room = None
    rs = None
    try:
        room = Room.objects.get(pk=room_id, pg=pg)
    except Exception:
        errors.append('Invalid room selection.')
    try:
        share_no = int(share_no_raw) if share_no_raw is not None else None
    except Exception:
        share_no = None
        errors.append('Invalid share selection.')
    if room and share_no is not None:
        rs = RoomShareStatus.objects.filter(room=room, share_no=share_no).first()
        if not rs:
            errors.append('Share not found for room.')

    today = timezone.now().date()
    if not joining_raw:
        errors.append('Joining date is required.')
    joining_date = parse_date(joining_raw) if joining_raw else None
    if joining_date is None:
        errors.append('Enter a valid joining date.')
    else:
        # Allow past joining date only if PG explicitly permits it
        if joining_date < today and not getattr(pg, 'past_joining_date_allowed', False):
            errors.append('Joining date cannot be in the past.')

    # Prepare application form data (ensure date_of_admission mirrors joining_date)
    data = request.POST.copy()
    if not data.get('date_of_admission'):
        data['date_of_admission'] = joining_date.isoformat()
    # Always enforce email to be current user's email (readonly in form)
    data['email'] = request.user.email
    form = ResidentApplicationForm(data, request.FILES)

    # Enforce DOB must be before 2010-01-01
    dob_raw = data.get('dob')
    if dob_raw:
        try:
            dob_val = parse_date(dob_raw)
            if dob_val and dob_val >= timezone.datetime(2010,1,1).date():
                errors.append('Date of Birth must be before the year 2010.')
        except Exception:
            pass

    # Validate share availability
    if rs:
        # Determine whether the share is available at the requested joining_date
        can_book_on_date = False
        if joining_date:
            if rs.status == RoomShareStatus.VACANT:
                can_book_on_date = True
            elif rs.status == RoomShareStatus.VACANT_FROM:
                # If vacant_from is not set or is on/before the joining_date, it's selectable
                can_book_on_date = (not rs.vacant_from) or (rs.vacant_from <= joining_date)

        if not can_book_on_date:
            current = (
                Booking.objects.filter(room=room, share_no=share_no, status=Booking.APPROVED)
                .order_by('-created_at').first()
            )
            # If there's no current booking or leaving date doesn't free it before requested joining_date, reject
            if not current or not current.leaving_date or not (joining_date and joining_date > current.leaving_date):
                errors.append('Selected share is not yet available for the chosen date.')
        
        # Check for pending future swaps targeting this bed
        if room and share_no:
            has_future_swap = RoomSwap.objects.filter(
                to_room=room,
                to_share_no=share_no,
                is_future_swap=True,
                status__in=[RoomSwap.PENDING, RoomSwap.APPROVED]
            ).exists()
            if has_future_swap:
                errors.append('This bed has a pending room swap and is not available for booking.')

    # Validate application form last, accumulate errors
    if not form.is_valid():
        # Collect field errors into errors list (brief)
        for fld, errs in form.errors.items():
            for er in errs:
                errors.append(f"{fld}: {er}")

    # Validate payment day selection (shown as "Final Joining Date" in UI)
    payment_day_raw = request.POST.get('payment_day', '')
    payment_date_selected = None
    if not payment_day_raw:
        errors.append('Final joining date is required. Please select a joining date first.')
    else:
        try:
            payment_date_selected = parse_date(payment_day_raw)
            if not payment_date_selected:
                errors.append('Invalid final joining date format.')
            elif joining_date:
                # Normalize: extract day-of-month and place it within 31 days of joining
                payment_date_selected = _normalize_payment_day(payment_date_selected, joining_date)
        except (ValueError, TypeError):
            errors.append('Invalid final joining date.')

    # Validate declaration checkbox
    decl_agreed = request.POST.get('decl_agreed')
    if decl_agreed != 'on':
        errors.append('You must agree to the declaration.')

    # Enforce mandatory files: selfie and Aadhaar/other must be provided
    selfie_file = request.FILES.get('selfie')
    aadhaar_in_form = form.cleaned_data.get('aadhaar_pdf') if hasattr(form, 'cleaned_data') else None
    if not selfie_file:
        errors.append('Selfie photo is required.')
    if not aadhaar_in_form:
        # Either no files or validation failed; add explicit error
        errors.append('Aadhaar Document 1 is required.')

    if errors:
        context.update({
            'errors': errors,
            'form': form,
            'selected_room_id': room_id,
            'selected_share_no': share_no_raw,
            'joining_date_value': joining_raw,
        })
        return render(request, 'bookings/quick_booking.html', context)

    # All validations passed; create booking and application inside a transaction
    try:
        with transaction.atomic():
            # Use the selected payment date (final joining date)
            payment_date_calculated = payment_date_selected if payment_date_selected else joining_date

            booking_obj = Booking.objects.create(
                user=request.user,
                room=room,
                pg=pg,
                share_no=share_no,
                status=Booking.PENDING,
                joining_date=joining_date,
                payment_date=payment_date_calculated,
            )
            # Reserve share when applicable
            if rs.status in [RoomShareStatus.VACANT, RoomShareStatus.VACANT_FROM]:
                rs.status = RoomShareStatus.RESERVED
                rs.save(update_fields=['status'])

            inst = form.save(commit=False)
            inst.user = request.user
            inst.booking = booking_obj
            inst.pg = pg
            inst.room = room
            inst.date_of_admission = joining_date
            # Set all declaration fields to True (user agreed to combined declaration)
            inst.decl_valuables = True
            inst.decl_notice = True
            inst.decl_deposit = True
            inst.decl_truth = True

            # Files handling with two separate Aadhaar fields
            selfie_file = request.FILES.get('selfie')
            aadhaar_file_1 = form.cleaned_data.get('aadhaar_pdf')
            aadhaar_file_2 = form.cleaned_data.get('aadhaar_pdf_2')
            
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            
            # Handle Aadhaar Document 1 (required)
            folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
            if aadhaar_file_1:
                name = (getattr(aadhaar_file_1, 'name', '') or '').lower()
                ctype = getattr(aadhaar_file_1, 'content_type', '') or ''
                is_pdf = ctype == 'application/pdf' or name.endswith('.pdf')
                
                if is_pdf:
                    # Upload PDF
                    up = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                else:
                    # Upload image (front side)
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    ext1 = _pick_ext(name)
                    up1 = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                    
                    # Handle Aadhaar Document 2 (optional - back side)
                    if aadhaar_file_2:
                        name2 = (getattr(aadhaar_file_2, 'name', '') or '').lower()
                        ext2 = _pick_ext(name2)
                        up2 = drive_upload(aadhaar_file_2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2
                    else:
                        inst.aadhaar_file_url_2 = ''


            inst.status = ResidentApplication.SUBMITTED
            inst.save()
            ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Submitted by user')

            # Persist phone number to the user's Profile
            try:
                from accounts.models import Profile as _Profile
                phone_norm = (form.cleaned_data.get('phone') or '').strip()
                if phone_norm:
                    prof, _ = _Profile.objects.get_or_create(user=request.user)
                    if prof.phone != phone_norm:
                        prof.phone = phone_norm
                        prof.save(update_fields=['phone'])
            except Exception:
                # Non-fatal: failure to update phone shouldn't block booking
                pass

            # Notify admins (best-effort)
            try:
                admin_path = f"{reverse('pg_resident_applications')}?{urlencode({'pg': pg.id, 'email': inst.user.email or ''})}"
                admin_url = request.build_absolute_uri(admin_path)
                admin_profiles = list(pg.admins.select_related('user').all())
                for ap in admin_profiles:
                    Notification.objects.create(
                        user=ap.user,
                        title="Resident Application Submitted",
                        message=(
                            f"{inst.user.email} submitted an application for Room {room.room_no} Share {share_no}. "
                            f"Review and confirm: {admin_url}"
                        ),
                    )
                send_push_to_users(
                    [ap.user for ap in admin_profiles],
                    title="Resident Application Submitted",
                    body=f"{inst.user.email} submitted application for Room {room.room_no}, Bed {share_no}.",
                    url=admin_path,
                    extra_data={'type': 'application_submitted', 'application_status': inst.status, 'pg_id': pg.id},
                )
                admin_emails = [ap.user.email for ap in admin_profiles if getattr(ap.user, 'email', None)]
                if admin_emails:
                    send_mail(
                        subject="PG-MS: Resident Application Submitted",
                        message=(
                            f"A resident application was submitted and awaits your confirmation.\n\n"
                            f"PG: {pg.name}\n"
                            f"Room: {room.room_no} | Share: {share_no}\n"
                            f"Applicant: {inst.name or inst.user.get_full_name() or inst.user.email}\n"
                            f"Email: {inst.user.email}\n\n"
                            f"View and confirm here: {admin_url}\n"
                        ),
                        from_email=None,
                        recipient_list=admin_emails,
                        fail_silently=True,
                    )
            except Exception:
                pass
    except IntegrityError:
        errors.append("You already have an active booking in this PG.")
    except Exception as ex:
        errors.append('Failed to create booking/application. Please try again.')

    if errors:
        context.update({
            'errors': errors,
            'form': form,
            'selected_room_id': room_id,
            'selected_share_no': share_no_raw,
            'joining_date_value': joining_raw,
        })
        return render(request, 'bookings/quick_booking.html', context)

    messages.success(request, 'Booking request and application submitted.')
    return redirect('dashboard')


@login_required
def pg_quick_rooms(request, pgslug):
    pg = _pg_by_slug_or_404(pgslug)
    rooms = (
        Room.objects.filter(pg=pg)
        .order_by('room_no')
        .prefetch_related('shares')
    )
    # Only include rooms that have at least one share that is VACANT or VACANT_FROM (even if future-dated)
    today = timezone.now().date()
    include_vacant_from = request.GET.get('include_vacant_from', 'false').lower() == 'true'
    
    # Get all beds with pending/approved future swaps for this PG
    beds_with_future_swaps = set(
        RoomSwap.objects.filter(
            to_room__pg=pg,
            is_future_swap=True,
            status__in=[RoomSwap.PENDING, RoomSwap.APPROVED]
        ).values_list('to_room_id', 'to_share_no')
    )
    
    def share_is_available(rs: RoomShareStatus):
        # Check if this bed has a pending future swap
        if (rs.room_id, rs.share_no) in beds_with_future_swaps:
            return False
        if rs.status == RoomShareStatus.VACANT:
            return True
        if include_vacant_from and rs.status == RoomShareStatus.VACANT_FROM:
            return True
        return False
    
    data = []
    for r in rooms:
        available_shares = [s for s in r.shares.all() if share_is_available(s)]
        if available_shares:
            total_shares = r.shares.count()
            data.append({
                'id': r.id,
                'room_no': r.room_no,
                'vacant_beds': [s.share_no for s in available_shares],
                'bed_count': total_shares,
                'available_count': len(available_shares),
                # Legacy aliases retained for clients still using share terminology
                'vacant_shares': [s.share_no for s in available_shares],
                'share_count': total_shares,
            })
    return JsonResponse({'ok': True, 'rooms': data})


@login_required
def pg_quick_shares(request, pgslug, room_id):
    pg = _pg_by_slug_or_404(pgslug)
    room = get_object_or_404(Room, pk=room_id, pg=pg)
    today = timezone.now().date()
    include_vacant_from = request.GET.get('include_vacant_from', 'false').lower() == 'true'
    shares = RoomShareStatus.objects.filter(room=room).order_by('share_no')
    
    # Get beds with pending/approved future swaps for this room
    beds_with_future_swaps = set(
        RoomSwap.objects.filter(
            to_room=room,
            is_future_swap=True,
            status__in=[RoomSwap.PENDING, RoomSwap.APPROVED]
        ).values_list('to_share_no', flat=True)
    )
    
    result = []
    for s in shares:
        # Check if this bed has a pending future swap - if so, skip it entirely
        if s.share_no in beds_with_future_swaps:
            continue
        
        # Include based on include_vacant_from parameter
        if s.status == RoomShareStatus.VACANT:
            include_this = True
            selectable = True
        elif s.status == RoomShareStatus.VACANT_FROM:
            include_this = include_vacant_from
            selectable = (not s.vacant_from or s.vacant_from <= today)
        else:
            include_this = False
            selectable = False
        
        if include_this:
            result.append({
                'bed_no': s.share_no,
                'share_no': s.share_no,
                'available_from': s.vacant_from.isoformat() if s.vacant_from else None,
                'vacant_from': s.vacant_from.isoformat() if s.vacant_from else None,
                'status': s.status,
                'selectable': selectable,
            })
    
    selectable_beds = [x['share_no'] for x in result if x['selectable']]
    return JsonResponse({
        'ok': True,
        'room_id': room.id,
        'vacant_beds': selectable_beds,
        'beds': result,
        # Legacy aliases for backward compatibility
        'vacant_shares': selectable_beds,
        'shares': result,
    })


def _user_pg(user):
    # Legacy helper (unused for selection now). Keeping for compatibility.
    pg = PG.objects.filter(admins__user=user).first()
    if not pg:
        bk = Booking.objects.filter(user=user, status=Booking.APPROVED).select_related('room__pg').first()
        if bk:
            pg = bk.room.pg
    return pg


@login_required
def availability(request):
    # Let users pick a PG; if none selected, show the list of PGs first
    pgs = PG.objects.all().order_by('name')
    pg_id = request.GET.get('pg')
    pg = PG.objects.filter(pk=pg_id).first() if pg_id else None
    rooms = Room.objects.filter(pg=pg).prefetch_related('shares').order_by('room_no') if pg else []
    # Preload approved bookings to find leaving dates (confirmed/unconfirmed) for the selected PG
    leaving_map = {}
    today = timezone.now().date()
    
    # Get beds with pending future swaps for this PG
    beds_with_future_swaps = set()
    if pg:
        beds_with_future_swaps = set(
            RoomSwap.objects.filter(
                to_room__pg=pg,
                is_future_swap=True,
                status__in=[RoomSwap.PENDING, RoomSwap.APPROVED]
            ).values_list('to_room_id', 'to_share_no')
        )
    
    if pg:
        qs = (
            Booking.objects.filter(status=Booking.APPROVED, room__pg=pg, leaving_date__isnull=False)
            .only('room_id', 'share_no', 'leaving_date', 'leaving_confirmed_date', 'id')
            .order_by('room_id', 'share_no', '-created_at')
        )
        for b in qs:
            key = f"{b.room_id}:{b.share_no}"
            if key not in leaving_map:
                # Earliest entry per share (most recent booking due to ordering) captures leaving info
                leaving_map[key] = {
                    'leaving_date': b.leaving_date,
                    'confirmed': bool(b.leaving_confirmed_date),
                    'confirmed_date': b.leaving_confirmed_date,
                    'available_from': b.leaving_date + timedelta(days=1) if b.leaving_date else None,
                }
        # Attach leaving map data and future swap info directly to share objects for simpler template access
        for room_obj in rooms:
            for share in room_obj.shares.all():
                key = f"{room_obj.id}:{share.share_no}"
                setattr(share, 'leaving_data', leaving_map.get(key))
                setattr(share, 'has_future_swap', (room_obj.id, share.share_no) in beds_with_future_swaps)
    return render(request, 'bookings/availability.html', {"pg": pg, "pgs": list(pgs), "rooms": rooms, "leaving_map": leaving_map, "today": today})


@login_required
@transaction.non_atomic_requests
def request_booking(request, room_id, share_no):
    room = get_object_or_404(Room, pk=room_id)
    share = get_object_or_404(RoomShareStatus, room=room, share_no=share_no)
    # Enforce: Only one active (pending/approved) booking per user per PG
    # First, lazily complete any of this user's bookings whose leaving date has passed and was confirmed.
    today = timezone.now().date()
    stale_qs = (
        Booking.objects.filter(
            user=request.user,
            status=Booking.APPROVED,
            leaving_date__isnull=False,
            leaving_date__lte=today,
            leaving_confirmed_date__isnull=False,
        )
        .select_related('room')
        .order_by('room_id', 'share_no')
    )
    for bk in stale_qs:
        try:
            with transaction.atomic():
                # Free the share if still not marked VACANT
                rs = RoomShareStatus.objects.filter(room=bk.room, share_no=bk.share_no).first()
                if rs and rs.status != RoomShareStatus.VACANT:
                    rs.status = RoomShareStatus.VACANT
                    if rs.vacant_from:
                        rs.vacant_from = None
                        rs.save(update_fields=['status', 'vacant_from'])
                    else:
                        rs.save(update_fields=['status'])
                # Mark booking completed
                if bk.status != Booking.COMPLETED:
                    bk.status = Booking.COMPLETED
                    bk.save(update_fields=['status'])
        except Exception:
            # Best-effort; if this fails, constraint will still prevent duplicate active bookings.
            pass
    # Only block if user already has a pending/approved booking in THIS PG.
    # Users who left (COMPLETED or leaving_confirmed_date in the past) can book again in the same PG.
    has_active = Booking.objects.filter(
        user=request.user,
        room__pg=room.pg,
        status__in=[Booking.PENDING, Booking.APPROVED],
    ).filter(
        Q(leaving_confirmed_date__isnull=True) | Q(leaving_confirmed_date__gt=today)
    ).exists()
    if has_active:
        messages.error(request, "You already have an active booking in this PG. You can book in another PG, but only one booking per PG is allowed.")
        return redirect('dashboard')
    
    # Check if this bed has a pending future swap - if so, don't allow booking
    has_future_swap = RoomSwap.objects.filter(
        to_room=room,
        to_share_no=share_no,
        is_future_swap=True,
        status__in=[RoomSwap.PENDING, RoomSwap.APPROVED]
    ).exists()
    if has_future_swap:
        messages.error(request, "This bed has a pending room swap and is not available for booking.")
        return redirect('availability')

    if request.method == 'POST':
        form = BookingRequestForm(request.POST)
        if form.is_valid():
            joining_date = form.cleaned_data['joining_date']
            today = timezone.now().date()
            if joining_date < today:
                messages.error(request, "Joining date cannot be in the past.")
                return render(request, 'bookings/request_booking.html', {"form": form, "room": room, "share": share})

            # If share is vacant OR scheduled vacancy date already reached, allow any today-or-future date
            can_treat_vacant = (
                share.status == RoomShareStatus.VACANT
                or (share.status == RoomShareStatus.VACANT_FROM and (not share.vacant_from or share.vacant_from <= today))
            )
            if can_treat_vacant:
                try:
                    with transaction.atomic():  # savepoint to avoid breaking outer transaction on IntegrityError
                        booking_obj = Booking.objects.create(
                            user=request.user,
                            room=room,
                            pg=room.pg,
                            share_no=share_no,
                            status=Booking.PENDING,
                            joining_date=joining_date,
                        )
                        # Mark share reserved
                        share.status = RoomShareStatus.RESERVED
                        share.save(update_fields=['status'])
                except IntegrityError:
                    messages.error(request, "You already have an active booking in this PG.")
                    return render(request, 'bookings/request_booking.html', {"form": form, "room": room, "share": share})
            else:
                # If occupied, allow booking only if current occupant has a leaving_date and joining > leaving_date
                current = (
                    Booking.objects.filter(room=room, share_no=share_no, status=Booking.APPROVED)
                    .order_by('-created_at')
                    .first()
                )
                if not current or not current.leaving_date:
                    # If UI showed it as bookable due to VACANT_FROM date having passed, allow booking even if no current approved row is found
                    if share.status == RoomShareStatus.VACANT_FROM and (not share.vacant_from or share.vacant_from <= today):
                        try:
                            with transaction.atomic():
                                booking_obj = Booking.objects.create(
                                    user=request.user,
                                    room=room,
                                    pg=room.pg,
                                    share_no=share_no,
                                    status=Booking.PENDING,
                                    joining_date=joining_date,
                                )
                                share.status = RoomShareStatus.RESERVED
                                share.save(update_fields=['status'])
                        except IntegrityError:
                            messages.error(request, "You already have an active booking in this PG.")
                            return render(request, 'bookings/request_booking.html', {"form": form, "room": room, "share": share})
                    else:
                        messages.error(request, "This share is currently occupied and not scheduled to be vacated.")
                        return render(request, 'bookings/request_booking.html', {"form": form, "room": room, "share": share})
                # Require PG Admin confirmation before allowing future booking
                if not (joining_date > current.leaving_date):
                    messages.error(request, f"Joining date must be after the occupant's leaving date ({current.leaving_date}).")
                    return render(
                        request,
                        'bookings/request_booking.html',
                        {"form": form, "room": room, "share": share, "available_from": current.leaving_date + timedelta(days=1)},
                    )
                try:
                    with transaction.atomic():
                        booking_obj = Booking.objects.create(
                            user=request.user,
                            room=room,
                            pg=room.pg,
                            share_no=share_no,
                            status=Booking.PENDING,
                            joining_date=joining_date,
                        )
                        # For an occupied share with a confirmed future leaving, keep VACANT_FROM so schedule is visible;
                        # only mark RESERVED if it was previously VACANT (rare race) or OCCUPIED without leaving schedule.
                        if share.status in [RoomShareStatus.OCCUPIED, RoomShareStatus.VACANT, RoomShareStatus.VACANT_FROM] and share.status != RoomShareStatus.RESERVED:
                            share.status = RoomShareStatus.RESERVED
                            share.save(update_fields=['status'])
                except IntegrityError:
                    messages.error(request, "You already have an active booking in this PG.")
                    return render(request, 'bookings/request_booking.html', {"form": form, "room": room, "share": share})

            # Notify PG admins (all admins of this PG) + optionally site admins
            try:
                pending_path = f"{reverse('pg_bookings_pending')}?{urlencode({'pg': room.pg_id})}"
                pending_link = request.build_absolute_uri(pending_path)
                # PG admins
                pg_admin_profiles = list(room.pg.admins.select_related('user').all())
                pg_admin_emails = [ap.user.email for ap in pg_admin_profiles if ap.user.email]
                # Create in-app notifications for PG admins
                for ap in pg_admin_profiles:
                    Notification.objects.create(
                        user=ap.user,
                        title="New booking request",
                        message=f"{request.user.email} requested Room {room.room_no} Share {share_no} (Joining {joining_date}).",
                    )

                send_push_to_users(
                    [ap.user for ap in pg_admin_profiles],
                    title="New Booking Request",
                    body=f"{request.user.email} requested Room {room.room_no}, Bed {share_no}.",
                    url=pending_path,
                    extra_data={'type': 'booking_request', 'booking_type': 'regular', 'pg_id': room.pg_id},
                )
                # (Optional) Site admins still receive it for oversight
                site_admin_emails = list(
                    Profile.objects.filter(is_website_admin=True, status='active').values_list('user__email', flat=True)
                )
                recipient_list = sorted(set(pg_admin_emails + site_admin_emails))
                if recipient_list:
                    send_mail(
                        subject="PG-MS: New Booking Request",
                        message=(
                            "A new booking request was submitted.\n"
                            f"PG: {room.pg.name}\n"
                            f"Room: {room.room_no} | Share: {share_no}\n"
                            f"User: {request.user.email}\n"
                            f"Joining Date: {joining_date}\n\n"
                            f"Review pending bookings: {pending_link}"
                        ),
                        from_email=None,
                        recipient_list=recipient_list,
                        fail_silently=True,
                    )
            except Exception:
                pass

            log(request.user, 'booking_requested', 'Room', room.id, f"Share {share_no} joining {joining_date}")
            messages.success(request, "Booking request submitted. Await PG Admin approval.")
            return redirect('dashboard')
    else:
        form = BookingRequestForm()

    # If occupied+leaving, show available_from for hint
    available_from = None
    if share.status in [RoomShareStatus.OCCUPIED, RoomShareStatus.VACANT_FROM]:
        current = Booking.objects.filter(room=room, share_no=share_no, status=Booking.APPROVED).order_by('-created_at').first()
        if current and current.leaving_date:
            available_from = current.leaving_date + timedelta(days=1)
    return render(request, 'bookings/request_booking.html', {"form": form, "room": room, "share": share, "available_from": available_from})


@login_required
def aadhaar_submit(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user, status=Booking.APPROVED)
    form = AadhaarForm()
    if request.method == 'POST':
        form = AadhaarForm(request.POST, request.FILES)
        if form.is_valid():
            request.user.profile.aadhaar_number = form.cleaned_data['aadhaar_number']
            file = request.FILES['aadhaar_file']
            up = drive_upload(file, f"aadhaar_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', ''))
            if up:
                _fid, preview = up
                request.user.profile.aadhaar_file_url = preview
            request.user.profile.save()
            log(request.user, 'aadhaar_submitted', 'Booking', booking.id)
            messages.success(request, "Aadhaar submitted.")
            return redirect('dashboard')
    return render(request, 'bookings/aadhaar_submit.html', {"form": form, "booking": booking})


@login_required
def leaving_intimation(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user)
    if request.method == 'POST':
        leaving_raw = request.POST.get('leaving_date')
        leaving_date = parse_date(leaving_raw) if leaving_raw else None
        # Validate: leaving date must be on/after joining date
        min_allowed = booking.joining_date or booking.start_date
        if leaving_date is None:
            messages.error(request, "Please select a valid leaving date.")
            return redirect('dashboard')
        if min_allowed and leaving_date < min_allowed:
            messages.error(request, f"Leaving date must be on or after your joining date ({min_allowed}).")
            return redirect('dashboard')
        booking.leaving_date = leaving_date
        booking.save(update_fields=['leaving_date'])
        # Mark share as pending vacancy
        try:
            share = RoomShareStatus.objects.get(room=booking.room, share_no=booking.share_no)
            if share.status == RoomShareStatus.OCCUPIED:
                share.status = RoomShareStatus.VACANT_FROM
                share.vacant_from = booking.leaving_date
                share.save(update_fields=['status','vacant_from'])
        except RoomShareStatus.DoesNotExist:
            pass
        # Notify PG Admins (simple: all admins of this PG)
        pg = booking.room.pg
        admin_users = _pg_admin_users(pg)
        leave_requests_path, leave_payload = _leave_admin_path_and_payload(pg.id, booking)
        # Create in-app notifications for each admin
        for admin_user in admin_users:
            Notification.objects.create(
                user=admin_user,
                title="Leaving request",
                message=f"{request.user.email} plans to leave on {leaving_date} (Room {booking.room.room_no}, Share {booking.share_no}).",
            )
        send_push_to_users(
            admin_users,
            title="Leaving Request",
            body=f"{request.user.email} plans to leave on {leaving_date}.",
            url=leave_requests_path,
            extra_data={**leave_payload, 'type': 'leave_requested', 'source': 'leaving_intimation'},
        )
        # Send single email to all admin emails (if any)
        try:
            admin_emails = [admin_user.email for admin_user in admin_users if getattr(admin_user, 'email', None)]
            if admin_emails:
                tenant_name = request.user.get_full_name() or request.user.email
                send_mail(
                    subject="PG-MS: Leaving Request",
                    message=(
                        f"Tenant {tenant_name} plans to leave on {leaving_date}.\n"
                        f"PG: {pg.name}\nRoom: {booking.room.room_no} | Share: {booking.share_no}\n"
                        "Review and confirm in Leaving Requests page."
                    ),
                    from_email=None,
                    recipient_list=admin_emails,
                    fail_silently=True,
                )
        except Exception:
            pass
        log(request.user, 'leaving_requested', 'Booking', booking.id, f"Leaving {leaving_date}")
        messages.success(request, "Leaving date submitted. PG Admin will be notified.")
        return redirect('dashboard')
    return render(request, 'bookings/leaving_intimation.html', {"booking": booking})


@login_required
def booking_detail(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id)
    booking_pg_id = getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None)
    # Authorization: only the booking owner, superuser/site-admin, or a PG Admin of this booking's PG can view
    can_view = False
    can_delete = False
    is_admin = False
    
    if request.user == booking.user:
        can_view = True
    elif getattr(request.user, 'is_superuser', False) or (hasattr(request.user, 'profile') and getattr(request.user.profile, 'is_website_admin', False)):
        can_view = True
        can_delete = True  # Superusers and website admins can always delete
        is_admin = True
    else:
        if booking_pg_id:
            pg_admin = PGAdmin.objects.filter(user=request.user, pg_id=booking_pg_id).first()
            if pg_admin:
                can_view = True
                is_admin = True
                # Check delete permission
                try:
                    from pgadmin.models import PGAdminPermission
                    perm = PGAdminPermission.objects.filter(pg_admin=pg_admin).first()
                    if perm and perm.can_delete_confirmed_bookings:
                        can_delete = True
                except Exception:
                    pass
    
    if not can_view:
        messages.error(request, "You do not have permission to view this booking.")
        return redirect('dashboard')

    # Keep admin PG context aligned when opening booking details from deep links.
    if is_admin and booking_pg_id:
        request.session['active_pg_id'] = booking_pg_id

    from core.models import AuditLog
    from django.db.models import Q
    # Get logs for both the booking and its application (if any)
    app_id = getattr(getattr(booking, 'application', None), 'id', None)
    if app_id:
        events = AuditLog.objects.filter(
            Q(target_type='Booking', target_id=booking.id) |
            Q(target_type='ResidentApplication', target_id=app_id)
        ).order_by('created_at')
    else:
        events = AuditLog.objects.filter(target_type='Booking', target_id=booking.id).order_by('created_at')
    return render(request, 'bookings/booking_detail.html', {
        "booking": booking, 
        "events": events,
        "can_delete": can_delete,
        "is_admin": is_admin,
    })


@login_required
def application_fill(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user)
    from .models import ResidentApplication
    app = getattr(booking, 'application', None)
    # If confirmed, redirect to dashboard (form is no longer accessible)
    if app and getattr(app, 'status', None) == ResidentApplication.CONFIRMED:
        messages.info(request, "Your application has been confirmed and cannot be modified. Contact PG Admin if you need changes.")
        return redirect('dashboard')
    # For other statuses, user can view/modify the application
    elif app:
        # Show appropriate message based on status
        if app.status == ResidentApplication.REFILL_REQUESTED:
            messages.info(request, "PG Admin has requested you to refill/update your application. Please review and resubmit.")
        elif app.status in [ResidentApplication.SUBMITTED, ResidentApplication.RESUBMITTED]:
            messages.info(request, "You can modify your application below until it is confirmed by PG Admin.")
    if request.method == 'POST':
        form = ResidentApplicationForm(request.POST, request.FILES, instance=app)
        
        # Validate declaration checkbox first
        decl_agreed = request.POST.get('decl_agreed')
        if decl_agreed != 'on':
            messages.error(request, "You must agree to the declaration.")
            return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
        
        # Validate form first to get cleaned_data
        if not form.is_valid():
            messages.error(request, "Please correct the errors in the form.")
            return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
        
        # Now validate payment day after form is valid
        payment_day_raw = request.POST.get('payment_day', '')
        
        if not payment_day_raw:
            messages.error(request, "Final joining date is required. Please select admission date first.")
            return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
        
        try:
            payment_date_selected = parse_date(payment_day_raw)
            if not payment_date_selected:
                messages.error(request, "Invalid final joining date format.")
                return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
            # Normalize: extract day-of-month and place within 31 days of admission
            admission_date = form.cleaned_data.get('date_of_admission')
            if admission_date:
                payment_date_selected = _normalize_payment_day(payment_date_selected, admission_date)
        except (ValueError, TypeError):
            messages.error(request, "Invalid final joining date.")
            return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
        
        # File validation - allow refill without re-uploading files
        selfie_file = request.FILES.get('selfie')
        aadhaar_file_1 = form.cleaned_data.get('aadhaar_pdf')
        
        # Enforce selfie mandatory only if it doesn't exist already
        if not selfie_file and not (app and app.selfie_url):
            messages.error(request, "Selfie is required. Capture or upload a clear face photo.")
            return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
        
        # Aadhaar file required only on first submission or if not already uploaded
        if not aadhaar_file_1 and not (app and app.aadhaar_file_url):
            messages.error(request, "Aadhaar Document 1 is required.")
            return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
        
        # Save the application
        inst = form.save(commit=False)
        inst.user = request.user
        inst.booking = booking
        inst.pg = booking.room.pg
        inst.room = booking.room
        # Set all declaration fields to True (user agreed to combined declaration)
        inst.decl_valuables = True
        inst.decl_notice = True
        inst.decl_deposit = True
        inst.decl_truth = True
        
        # Update booking's payment_date based on selected payment_day (full date from form)
        # The form submits a full date (YYYY-MM-DD), so we use it directly
        if payment_date_selected and booking.payment_date != payment_date_selected:
            booking.payment_date = payment_date_selected
            booking.save(update_fields=['payment_date'])
        
        # Upload files to Drive with two separate Aadhaar fields
        aadhaar_file_2 = form.cleaned_data.get('aadhaar_pdf_2')

        # --- Selfie ---
        if selfie_file:
            up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
            if up:
                _fid, preview = up
                inst.selfie_url = preview
        elif app:
            # No new selfie uploaded — preserve existing
            inst.selfie_url = app.selfie_url

        # --- Aadhaar (always runs, independent of selfie) ---
        folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
        if aadhaar_file_1:
            old_url_1 = app.aadhaar_file_url if app else None
            old_url_2 = getattr(app, 'aadhaar_file_url_2', None) if app else None

            name = (getattr(aadhaar_file_1, 'name', '') or '').lower()
            ctype = getattr(aadhaar_file_1, 'content_type', '') or ''
            is_pdf = ctype == 'application/pdf' or name.endswith('.pdf')

            if is_pdf:
                up = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}.pdf", folder)
                if up:
                    _fid, preview = up
                    inst.aadhaar_file_url = preview
                    inst.aadhaar_file_url_2 = ''
                    if old_url_1 and old_url_1 != preview:
                        try:
                            drive_delete(old_url_1)
                        except Exception:
                            pass
                    if old_url_2:
                        try:
                            drive_delete(old_url_2)
                        except Exception:
                            pass
            else:
                def _pick_ext(nm: str):
                    if nm.endswith('.png'): return '.png'
                    if nm.endswith('.webp'): return '.webp'
                    return '.jpg'

                ext1 = _pick_ext(name)
                up1 = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                if up1:
                    _fid1, preview1 = up1
                    inst.aadhaar_file_url = preview1
                    if old_url_1 and old_url_1 != preview1:
                        try:
                            drive_delete(old_url_1)
                        except Exception:
                            pass

                if aadhaar_file_2:
                    name2 = (getattr(aadhaar_file_2, 'name', '') or '').lower()
                    ext2 = _pick_ext(name2)
                    up2 = drive_upload(aadhaar_file_2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                    if up2:
                        _fid2, preview2 = up2
                        inst.aadhaar_file_url_2 = preview2
                        if old_url_2 and old_url_2 != preview2:
                            try:
                                drive_delete(old_url_2)
                            except Exception:
                                pass
                else:
                    inst.aadhaar_file_url_2 = ''
                    if old_url_2:
                        try:
                            drive_delete(old_url_2)
                        except Exception:
                            pass
        else:
            # No new Aadhaar uploaded — preserve existing
            if app:
                inst.aadhaar_file_url = app.aadhaar_file_url
                inst.aadhaar_file_url_2 = getattr(app, 'aadhaar_file_url_2', '')

        # --- Save instance and handle status transitions ---
        is_new = app is None
        inst.save()
        from .models import ApplicationStatusHistory
        if is_new:
            inst.status = ResidentApplication.SUBMITTED
            inst.save(update_fields=['status'])
            ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Submitted by user')
        else:
            if app.status == ResidentApplication.REFILL_REQUESTED:
                inst.status = ResidentApplication.RESUBMITTED
                inst.save(update_fields=['status'])
                ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Re-submitted by user')
            elif app.status in (ResidentApplication.REJECTED, ResidentApplication.SUBMITTED, ResidentApplication.RESUBMITTED):
                inst.status = ResidentApplication.SUBMITTED
                inst.save(update_fields=['status'])
                ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Updated by user')
            elif app.status == ResidentApplication.PENDING:
                inst.status = ResidentApplication.SUBMITTED
                inst.save(update_fields=['status'])
                ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Submitted by user')

        # Notify PG Admins via in-app notification and email with a link to review/confirm
        try:
            admin_path = f"{reverse('pg_resident_applications')}?{urlencode({'pg': inst.pg_id, 'email': inst.user.email or ''})}"
            admin_url = request.build_absolute_uri(admin_path)
            action = 'Submitted' if inst.status == ResidentApplication.SUBMITTED else ('Re-submitted' if inst.status == ResidentApplication.RESUBMITTED else 'Updated')
            # In-app notifications
            admin_profiles = list(inst.pg.admins.select_related('user').all())
            for ap in admin_profiles:
                Notification.objects.create(
                    user=ap.user,
                    title=f"Resident Application {action}",
                    message=(
                        f"{inst.user.email} {action.lower()} an application for Room {booking.room.room_no} Share {booking.share_no}. "
                        f"Review and confirm: {admin_url}"
                    ),
                )

            send_push_to_users(
                [ap.user for ap in admin_profiles],
                title=f"Application {action}",
                body=f"{inst.user.email} {action.lower()} application for Room {booking.room.room_no}, Bed {booking.share_no}.",
                url=admin_path,
                extra_data={'type': 'application_submitted', 'application_status': inst.status, 'pg_id': inst.pg_id},
            )
            # Email
            admin_emails = [ap.user.email for ap in admin_profiles if getattr(ap.user, 'email', None)]
            if admin_emails:
                send_mail(
                    subject=f"PG-MS: Resident Application {action}",
                    message=(
                        f"A resident application was {action.lower()} and awaits your confirmation.\n\n"
                        f"PG: {inst.pg.name}\n"
                        f"Room: {booking.room.room_no} | Share: {booking.share_no}\n"
                        f"Applicant: {inst.name or inst.user.get_full_name() or inst.user.email}\n"
                        f"Email: {inst.user.email}\n\n"
                        f"View and confirm here: {admin_url}\n"
                    ),
                    from_email=None,
                    recipient_list=admin_emails,
                    fail_silently=True,
                )
        except Exception:
            # Non-fatal notification/email errors shouldn't block form completion
            pass
        messages.success(request, "Application saved.")
        if request.GET.get('from') == 'self':
            return redirect('my_application')
        return redirect('dashboard')
    else:
        if app:
            form = ResidentApplicationForm(instance=app)
        else:
            initial = {
                'name': f"{request.user.first_name} {request.user.last_name}".strip(),
                'phone': request.user.profile.phone,
                'email': request.user.email,
                'date_of_admission': booking.start_date or booking.joining_date,
            }
            form = ResidentApplicationForm(initial=initial)
    
    # Pass full payment_date (not just day) for display in date input field
    # Priority: booking.payment_date (normalized) > booking.joining_date
    final_joining_date = None
    is_payment_date_locked = False
    _anchor = booking.joining_date or booking.start_date

    if booking.payment_date:
        if booking.status == Booking.APPROVED:
            # Locked: show as-is (do not normalize — admin set this deliberately)
            final_joining_date = booking.payment_date
            is_payment_date_locked = True
        else:
            # Not yet approved: normalize to be within 31 days of joining so the
            # form renders a sensible default even if admin set a far-future date.
            final_joining_date = (
                _normalize_payment_day(booking.payment_date, _anchor)
                if _anchor else booking.payment_date
            )
    elif _anchor:
        final_joining_date = _anchor
    
    return render(request, 'bookings/application_fill.html', {
        "form": form,
        "booking": booking,
        "app": app,
        "final_joining_date": final_joining_date,
        "is_payment_date_locked": is_payment_date_locked,
        "pg": booking.room.pg,
    })
from django.shortcuts import render

# Create your views here.

@login_required
def my_application(request):
    # Find the user's active booking (approved preferred, else pending)
    booking = (
        Booking.objects.filter(user=request.user, status=Booking.APPROVED)
        .select_related('room')
        .order_by('-created_at')
        .first()
        or Booking.objects.filter(user=request.user, status=Booking.PENDING)
        .select_related('room')
        .order_by('-created_at')
        .first()
    )
    if not booking:
        messages.info(request, "No active booking found to attach an application.")
        return redirect('dashboard')
    # Redirect to existing application form page, tagging source for redirect-back
    from django.urls import reverse
    return redirect(f"{reverse('application_fill', args=[booking.id])}?from=self")


# ============================================================================
# LEAVE PG FUNCTIONALITY
# ============================================================================

@login_required
def initiate_leave_request(request, booking_id):
    """User initiates leave request with notice period validation"""
    from .forms import LeaveRequestForm
    
    booking = get_object_or_404(
        Booking.objects.select_related('room', 'room__pg'),
        id=booking_id,
        user=request.user,
        status=Booking.APPROVED
    )
    
    # Check if already has pending leave request
    if booking.leaving_date and not booking.leaving_confirmed_date:
        messages.warning(request, "You already have a pending leave request. Please wait for confirmation or cancel it first.")
        return redirect('dashboard')
    
    # Check if already confirmed leaving
    if booking.leaving_confirmed_date:
        messages.info(request, "Your leave request has already been confirmed.")
        return redirect('dashboard')
    
    pg = booking.room.pg
    notice_period = getattr(pg, 'notice_period', 30)  # Default 30 days
    today = date.today()

    # Payment-cycle dates used by this page and server-side validation.
    payment_day = booking.payment_date.day if booking.payment_date else today.day
    next_payment_due_date, next_payment_date, day_one_adjustment = _recommended_leave_date(today, payment_day)
    following_payment_due_date, following_payment_date, _ = _recommended_leave_date(next_payment_due_date, payment_day)
    allow_custom_leave_date = bool(getattr(pg, 'allow_custom_leave_date', False))

    # Calculate notice period compliance for the quick-select leave dates.
    days_until_next_payment = (next_payment_date - today).days
    next_payment_eligible = days_until_next_payment >= notice_period
    
    days_until_following_payment = (following_payment_date - today).days
    following_payment_eligible = days_until_following_payment >= notice_period

    def _leave_context(form_obj):
        return {
            'form': form_obj,
            'booking': booking,
            'pg': pg,
            'next_payment_date': next_payment_date,
            'next_payment_due_date': next_payment_due_date,
            'following_payment_date': following_payment_date,
            'following_payment_due_date': following_payment_due_date,
            'day_one_adjustment': day_one_adjustment,
            'allow_custom_leave_date': allow_custom_leave_date,
            'next_payment_eligible': next_payment_eligible,
            'following_payment_eligible': following_payment_eligible,
            'notice_period': notice_period,
            'today': today,
            'payment_day': payment_day,
        }
    
    if request.method == 'POST':
        form = LeaveRequestForm(request.POST, booking=booking)
        if form.is_valid():
            leaving_date = form.cleaned_data['leaving_date']
            leaving_reason = form.cleaned_data['leaving_reason']
            acknowledge_no_advance = form.cleaned_data.get('acknowledge_no_advance', False)
            
            # Validate leaving date
            if leaving_date <= today:
                messages.error(request, "Leave date must be after today.")
                return render(request, 'bookings/leave_request.html', _leave_context(form))
            
            if booking.joining_date and leaving_date <= booking.joining_date:
                messages.error(request, "Leave date must be after your joining date.")
                return render(request, 'bookings/leave_request.html', _leave_context(form))

            # Server-side cap: user can never select a date after the following payment date - 1.
            if leaving_date > following_payment_date:
                messages.error(
                    request,
                    f"Leave date cannot be after {following_payment_date.strftime('%B %d, %Y')}."
                )
                return render(request, 'bookings/leave_request.html', _leave_context(form))

            # If custom date is disabled for this PG, only allow the two default capped dates.
            if not allow_custom_leave_date and leaving_date not in (next_payment_date, following_payment_date):
                messages.error(
                    request,
                    f"This booking allows only {next_payment_date.strftime('%B %d, %Y')} or {following_payment_date.strftime('%B %d, %Y')} as a leave date.",
                )
                return render(request, 'bookings/leave_request.html', _leave_context(form))
            
            # Calculate notice period compliance
            days_diff = (leaving_date - today).days
            advance_eligible = days_diff >= notice_period
            
            # If not eligible, require acknowledgment
            if not advance_eligible and not acknowledge_no_advance:
                messages.error(request, "You must acknowledge that no advance will be returned for early leaving.")
                return render(request, 'bookings/leave_request.html', _leave_context(form))
            
            # Save leave request
            booking.leaving_date = leaving_date
            booking.leaving_reason = leaving_reason
            booking.leaving_initiated_at = timezone.now()
            booking.advance_eligible = advance_eligible
            booking.save(update_fields=[
                'leaving_date', 'leaving_reason', 'leaving_initiated_at', 'advance_eligible'
            ])
            
            # Create notification for PG admin
            admin_users = _pg_admin_users(pg)
            leave_requests_path, leave_payload = _leave_admin_path_and_payload(pg.id, booking)
            for admin_user in admin_users:
                Notification.objects.create(
                    user=admin_user,
                    title="Leave Request Received",
                    message=f"{booking.user.get_full_name()} has requested to leave Room {booking.room.room_no}, Bed {booking.share_no} on {leaving_date.strftime('%B %d, %Y')}."
                )
            send_push_to_users(
                admin_users,
                title="Leave Request Received",
                body=f"{booking.user.get_full_name()} requested leave for Room {booking.room.room_no}, Bed {booking.share_no}.",
                url=leave_requests_path,
                extra_data={**leave_payload, 'type': 'leave_requested', 'source': 'leave_request'},
            )
            
            # Send emails
            try:
                from django.core.mail import send_mail
                from django.conf import settings
                tenant_name = booking.user.get_full_name() or booking.user.email
                subject = f"Leave Request Received - {pg.name}"
                message_body = (
                    f"A leave request has been initiated.\n\n"
                    f"PG Name: {pg.name}\n"
                    f"Tenant: {tenant_name}\n"
                    f"Room Number: {booking.room.room_no}\n"
                    f"Bed: {booking.share_no}\n"
                    f"Joining Date: {booking.joining_date}\n"
                    f"Leave Initiation Time: {timezone.localtime(booking.leaving_initiated_at).strftime('%I:%M %p')}\n"
                    f"Leave Date: {leaving_date}\n"
                )
                admin_emails = [a.email for a in admin_users if a.email]
                if booking.user.email:
                    send_mail(
                        subject,
                        message_body,
                        settings.DEFAULT_FROM_EMAIL,
                        [booking.user.email] + admin_emails,
                        fail_silently=True,
                    )
                elif admin_emails:
                    send_mail(
                        subject,
                        message_body,
                        settings.DEFAULT_FROM_EMAIL,
                        admin_emails,
                        fail_silently=True,
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to send leave raised emails: {e}")
            
            # Audit log
            log(
                actor=request.user,
                action='leave_initiated',
                target_type='Booking',
                target_id=booking.id,
                message=f"Leave request initiated for {leaving_date}",
                meta={
                    'leaving_date': leaving_date.isoformat(),
                    'advance_eligible': advance_eligible,
                    'reason': leaving_reason[:100] if leaving_reason else None
                }
            )
            
            messages.success(request, f"Leave request submitted successfully for {leaving_date.strftime('%B %d, %Y')}. Waiting for PG admin confirmation.")
            return redirect('dashboard')
    else:
        form = LeaveRequestForm(booking=booking, initial={'leaving_date': next_payment_date})

    return render(request, 'bookings/leave_request.html', _leave_context(form))


@login_required
def cancel_leave_request(request, booking_id):
    """User cancels their pending leave request"""
    booking = get_object_or_404(
        Booking,
        id=booking_id,
        user=request.user,
        status=Booking.APPROVED
    )
    
    # Can only cancel if not yet confirmed
    if booking.leaving_confirmed_date:
        messages.error(request, "Cannot cancel - leave has already been confirmed by PG admin.")
        return redirect('dashboard')
    
    if not booking.leaving_date:
        messages.info(request, "No pending leave request to cancel.")
        return redirect('dashboard')
    
    # Clear leave request
    old_leaving_date = booking.leaving_date
    booking.leaving_date = None
    booking.leaving_reason = ''
    booking.leaving_initiated_at = None
    booking.advance_eligible = True
    booking.save(update_fields=[
        'leaving_date', 'leaving_reason', 'leaving_initiated_at', 'advance_eligible'
    ])
    
    # Notify PG admin
    admin_users = _pg_admin_users(booking.room.pg)
    leave_requests_path, leave_payload = _leave_admin_path_and_payload(booking.room.pg_id, booking)
    for admin_user in admin_users:
        Notification.objects.create(
            user=admin_user,
            title="Leave Request Cancelled",
            message=f"{booking.user.get_full_name()} has cancelled their leave request for Room {booking.room.room_no}, Bed {booking.share_no} (was scheduled for {old_leaving_date})."
        )
    send_push_to_users(
        admin_users,
        title="Leave Request Cancelled",
        body=f"{booking.user.get_full_name()} cancelled leave for Room {booking.room.room_no}, Bed {booking.share_no}.",
        url=leave_requests_path,
        extra_data={**leave_payload, 'type': 'leave_cancelled'},
    )
    
    # Audit log
    log(
        actor=request.user,
        action='leave_cancelled',
        target_type='Booking',
        target_id=booking.id,
        message=f"Leave request cancelled (was scheduled for {old_leaving_date})",
        meta={'cancelled_date': old_leaving_date.isoformat()}
    )
    
    messages.success(request, "Leave request cancelled successfully.")
    return redirect('dashboard')


# ============================================================================
# DAY-WISE BOOKINGS MANAGEMENT (PG Admin)
# ============================================================================

def _require_pg_admin(user):
    """Check if user is a PG admin."""
    if getattr(user, 'is_superuser', False):
        return True
    if hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False):
        return True
    try:
        if PGAdmin.objects.filter(user=user).exists():
            return True
    except Exception:
        pass
    return hasattr(user, 'profile') and getattr(user.profile, 'is_pg_admin', False) and getattr(user.profile, 'status', 'active') == 'active'


def _admin_pgs(user):
    """PGs visible/manageable by the current user."""
    if getattr(user, 'is_superuser', False) or (hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False)):
        return PG.objects.all().order_by('name')
    return PG.objects.filter(admins__user=user).order_by('name')


def _active_pg(request):
    """Get currently active PG from session or query params."""
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
def daywise_bookings_list(request):
    """
    PG Admin view to list all day-wise bookings with filters and sorting.
    """
    if not _require_pg_admin(request.user):
        messages.error(request, "Access denied. PG Admin privileges required.")
        return redirect('dashboard')
    
    pg = _active_pg(request)
    pgs = list(_admin_pgs(request.user))
    
    # Auto-complete day-wise bookings where leaving_date is in the past
    today = date.today()
    if pg:
        expired_bookings = Booking.objects.filter(
            booking_type=Booking.DAYWISE,
            room__pg=pg,
            status=Booking.APPROVED,
            leaving_date__lt=today
        )
    else:
        expired_bookings = Booking.objects.filter(
            booking_type=Booking.DAYWISE,
            room__pg__in=pgs,
            status=Booking.APPROVED,
            leaving_date__lt=today
        )
    
    # Mark expired bookings as completed (don't change room share status)
    for booking in expired_bookings:
        booking.status = Booking.COMPLETED
        booking.save(update_fields=['status'])
    
    # Base queryset - only day-wise bookings (exclude rejected)
    if pg:
        bookings_qs = Booking.objects.filter(
            booking_type=Booking.DAYWISE,
            room__pg=pg
        ).exclude(status=Booking.REJECTED)
    else:
        bookings_qs = Booking.objects.filter(
            booking_type=Booking.DAYWISE,
            room__pg__in=pgs
        ).exclude(status=Booking.REJECTED)
    
    # Select related for performance (application is OneToOne)
    bookings_qs = bookings_qs.select_related(
        'user', 'room', 'room__pg', 'assigned_by', 'application', 'user__profile'
    )
    
    # Filters
    status_filter = request.GET.get('status', '').strip()
    payment_filter = request.GET.get('payment', '').strip()
    search_query = request.GET.get('search', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    
    if status_filter:
        bookings_qs = bookings_qs.filter(status=status_filter)
    
    if payment_filter == 'paid':
        bookings_qs = bookings_qs.filter(payment_received=True)
    elif payment_filter == 'unpaid':
        bookings_qs = bookings_qs.filter(payment_received=False)
    
    if search_query:
        bookings_qs = bookings_qs.filter(
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query) |
            Q(user__email__icontains=search_query) |
            Q(purpose__icontains=search_query) |
            Q(room__room_no__icontains=search_query)
        )
    
    if date_from:
        from_date = parse_date(date_from)
        if from_date:
            bookings_qs = bookings_qs.filter(joining_date__gte=from_date)
    
    if date_to:
        to_date = parse_date(date_to)
        if to_date:
            bookings_qs = bookings_qs.filter(joining_date__lte=to_date)
    
    # Sorting
    sort_by = request.GET.get('sort', '-created_at')
    valid_sorts = ['created_at', '-created_at', 'joining_date', '-joining_date', 
                   'leaving_date', '-leaving_date', 'status', '-status',
                   'payment_amount', '-payment_amount']
    if sort_by not in valid_sorts:
        sort_by = '-created_at'
    bookings_qs = bookings_qs.order_by(sort_by)
    
    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(bookings_qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Attach data to each booking (include application details if available)
    bookings_data = []
    for booking in page_obj:
        app = getattr(booking, 'application', None)
        bookings_data.append({
            'booking': booking,
            'application': app,
            'guest_name': (app.name if app else None) or booking.user.get_full_name() or booking.user.email,
            'phone': (app.phone if app else None) or (getattr(booking.user.profile, 'phone', '') if hasattr(booking.user, 'profile') else ''),
            'email': (app.email if app else None) or booking.user.email,
            'emergency_contact': app.emergency_contact if app else '',
            'selfie_url': app.selfie_url if app else '',
            'aadhaar_file_url': app.aadhaar_file_url if app else '',
            'aadhaar_file_url_2': getattr(app, 'aadhaar_file_url_2', '') if app else '',
            'days': (booking.leaving_date - booking.joining_date).days + 1 if booking.joining_date and booking.leaving_date else 0,
        })
    
    # Summary stats (exclude rejected)
    if pg:
        base_qs = Booking.objects.filter(booking_type=Booking.DAYWISE, room__pg=pg).exclude(status=Booking.REJECTED)
    else:
        base_qs = Booking.objects.filter(booking_type=Booking.DAYWISE, room__pg__in=pgs).exclude(status=Booking.REJECTED)
    
    total_daywise = base_qs.count()
    pending_count = base_qs.filter(status=Booking.PENDING).count()
    approved_count = base_qs.filter(status=Booking.APPROVED).count()
    completed_count = base_qs.filter(status=Booking.COMPLETED).count()
    active_today = base_qs.filter(
        status=Booking.APPROVED,
        joining_date__lte=today,
        leaving_date__gte=today
    ).count()
    
    context = {
        'pg': pg,
        'pgs': pgs,
        'bookings_data': bookings_data,
        'page_obj': page_obj,
        'today': today,
        # Filters
        'status_filter': status_filter,
        'payment_filter': payment_filter,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'sort_by': sort_by,
        # Stats
        'total_daywise': total_daywise,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'completed_count': completed_count,
        'active_today': active_today,
    }
    
    return render(request, 'bookings/daywise_bookings_list.html', context)
