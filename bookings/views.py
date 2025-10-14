import io
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.conf import settings
from django.utils import timezone

from accounts.models import Profile
from core.audit import log
from core.drive import drive_upload
from core.models import Notification
from django.core.mail import send_mail
from django.db import IntegrityError
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.http import JsonResponse, HttpResponseBadRequest
from pgadmin.models import PG, PGAdmin

from .application_forms import ResidentApplicationForm
from .forms import AadhaarForm, BookingRequestForm
from .models import Room, RoomShareStatus, Booking

def _pg_by_slug_or_404(slug: str):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(PG, slug=slug)

@login_required
def pg_quick_booking(request, pgslug):
    from .application_forms import ResidentApplicationForm
    from .models import ResidentApplication, ApplicationStatusHistory
    pg = _pg_by_slug_or_404(pgslug)
    has_active = Booking.objects.filter(user=request.user, room__pg=pg, status__in=[Booking.PENDING, Booking.APPROVED]).exists()

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
        return render(request, 'bookings/quick_booking.html', context)

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
    elif joining_date < today:
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
        can_book_now = (
            rs.status == RoomShareStatus.VACANT or (
                rs.status == RoomShareStatus.VACANT_FROM and (not rs.vacant_from or rs.vacant_from <= today)
            )
        )
        if not can_book_now:
            current = (
                Booking.objects.filter(room=room, share_no=share_no, status=Booking.APPROVED)
                .order_by('-created_at').first()
            )
            if not current or not current.leaving_date or not (joining_date > current.leaving_date):
                errors.append('Selected share is not yet available for the chosen date.')

    # Validate application form last, accumulate errors
    if not form.is_valid():
        # Collect field errors into errors list (brief)
        for fld, errs in form.errors.items():
            for er in errs:
                errors.append(f"{fld}: {er}")

    # Enforce mandatory files: selfie and Aadhaar/other must be provided
    selfie_file = request.FILES.get('selfie')
    aadhaar_in_form = form.cleaned_data.get('aadhaar_pdf') if hasattr(form, 'cleaned_data') else None
    if not selfie_file:
        errors.append('Selfie photo is required.')
    if not aadhaar_in_form:
        # Either no files or validation failed; add explicit error
        errors.append('Aadhaar/Document upload is required.')

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
            booking_obj = Booking.objects.create(
                user=request.user,
                room=room,
                pg=pg,
                share_no=share_no,
                status=Booking.PENDING,
                joining_date=joining_date,
                payment_date=joining_date,
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

            # Files handling (same rules as application_fill)
            selfie_file = request.FILES.get('selfie')
            aadhaar_files = form.cleaned_data.get('aadhaar_pdf') or []
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            if aadhaar_files:
                imgs, pdfs = [], []
                for f in aadhaar_files:
                    name = (getattr(f, 'name', '') or '').lower()
                    ctype = getattr(f, 'content_type', '') or ''
                    if ctype == 'application/pdf' or name.endswith('.pdf'):
                        pdfs.append(f)
                    elif ctype.startswith('image/') or any(name.endswith(ext) for ext in ('.jpg','.jpeg','.png','.webp')):
                        imgs.append(f)
                folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
                if pdfs:
                    f = pdfs[0]
                    up = drive_upload(f, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                elif imgs:
                    inst.aadhaar_file_url_2 = ''
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    f1 = imgs[0]
                    ext1 = _pick_ext((getattr(f1, 'name', '') or '').lower())
                    up1 = drive_upload(f1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                    if len(imgs) > 1:
                        f2 = imgs[1]
                        ext2 = _pick_ext((getattr(f2, 'name', '') or '').lower())
                        up2 = drive_upload(f2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2

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
                admin_url = request.build_absolute_uri(reverse('pg_resident_applications'))
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
    def share_is_available(rs: RoomShareStatus):
        return (rs.status == RoomShareStatus.VACANT) or (rs.status == RoomShareStatus.VACANT_FROM)
    data = []
    for r in rooms:
        available_shares = [s.share_no for s in r.shares.all() if share_is_available(s)]
        if available_shares:
            total_shares = r.shares.count()
            data.append({
                'id': r.id,
                'room_no': r.room_no,
                'vacant_beds': available_shares,
                'bed_count': total_shares,
                # Legacy aliases retained for clients still using share terminology
                'vacant_shares': available_shares,
                'share_count': total_shares,
            })
    return JsonResponse({'rooms': data})


@login_required
def pg_quick_shares(request, pgslug, room_id):
    pg = _pg_by_slug_or_404(pgslug)
    room = get_object_or_404(Room, pk=room_id, pg=pg)
    today = timezone.now().date()
    shares = RoomShareStatus.objects.filter(room=room).order_by('share_no')
    result = []
    for s in shares:
        # selectable if vacant now or vacancy date reached
        selectable = (
            s.status == RoomShareStatus.VACANT or (
                s.status == RoomShareStatus.VACANT_FROM and (not s.vacant_from or s.vacant_from <= today)
            )
        )
        result.append({
            'bed_no': s.share_no,
            'share_no': s.share_no,
            'available_from': s.vacant_from.isoformat() if s.vacant_from else None,
            'status': s.status,
            'selectable': selectable,
        })
    selectable_beds = [x['share_no'] for x in result if x['selectable']]
    return JsonResponse({
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
        # Attach leaving map data directly to share objects for simpler template access
        for room_obj in rooms:
            for share in room_obj.shares.all():
                key = f"{room_obj.id}:{share.share_no}"
                setattr(share, 'leaving_data', leaving_map.get(key))
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
    # Users who left (COMPLETED) can book again in the same PG.
    has_active = Booking.objects.filter(
        user=request.user,
        room__pg=room.pg,
        status__in=[Booking.PENDING, Booking.APPROVED],
    ).exists()
    if has_active:
        messages.error(request, "You already have an active booking in this PG. You can book in another PG, but only one booking per PG is allowed.")
        return redirect('dashboard')

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
                pending_link = request.build_absolute_uri(reverse('pg_bookings_pending'))
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
        admin_profiles = list(pg.admins.select_related('user').all())
        # Create in-app notifications for each admin
        for ap in admin_profiles:
            Notification.objects.create(
                user=ap.user,
                title="Leaving request",
                message=f"{request.user.email} plans to leave on {leaving_date} (Room {booking.room.room_no}, Share {booking.share_no}).",
            )
        # Send single email to all admin emails (if any)
        try:
            admin_emails = [ap.user.email for ap in admin_profiles if ap.user.email]
            if admin_emails:
                send_mail(
                    subject="PG-MS: Leaving Request",
                    message=(
                        f"Tenant {request.user.email} plans to leave on {leaving_date}.\n"
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
    # Authorization: only the booking owner, superuser/site-admin, or a PG Admin of this booking's PG can view
    can_view = False
    if request.user == booking.user:
        can_view = True
    elif getattr(request.user, 'is_superuser', False) or (hasattr(request.user, 'profile') and getattr(request.user.profile, 'is_website_admin', False)):
        can_view = True
    else:
        pg_id = getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None)
        if pg_id and PGAdmin.objects.filter(user=request.user, pg_id=pg_id).exists():
            can_view = True
    if not can_view:
        messages.error(request, "You do not have permission to view this booking.")
        return redirect('dashboard')
    from core.models import AuditLog
    events = AuditLog.objects.filter(target_type='Booking', target_id=booking.id).order_by('created_at')
    return render(request, 'bookings/booking_detail.html', {"booking": booking, "events": events})


@login_required
def application_fill(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user)
    from .models import ResidentApplication
    app = getattr(booking, 'application', None)
    # If confirmed, redirect to dashboard (form is no longer accessible)
    if app and getattr(app, 'status', None) == ResidentApplication.CONFIRMED:
        messages.info(request, "Your application has been confirmed. You can view it from your dashboard.")
        return redirect('dashboard')
    if request.method == 'POST':
        form = ResidentApplicationForm(request.POST, request.FILES, instance=app)
        if form.is_valid():
            inst = form.save(commit=False)
            inst.user = request.user
            inst.booking = booking
            inst.pg = booking.room.pg
            inst.room = booking.room
            # Enforce selfie mandatory (either newly uploaded or already existing)
            if not ((app and app.selfie_url) or request.FILES.get('selfie')):
                messages.error(request, "Selfie is required. Capture or upload a clear face photo.")
                return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
            # Aadhaar file (PDF or Image) required on first submission
            if app is None and not request.FILES.get('aadhaar_pdf'):
                messages.error(request, "Aadhaar document is required (PDF or Image).")
                return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
            # Upload files to Drive
            selfie_file = request.FILES.get('selfie')
            # Accept multiple files for Aadhaar/other card: either one PDF or up to two images
            aadhaar_files = form.cleaned_data.get('aadhaar_pdf') or []
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            else:
                # keep existing
                if app:
                    inst.selfie_url = app.selfie_url
            if aadhaar_files:
                # Separate images and PDFs by content type/extension
                imgs, pdfs = [], []
                for f in aadhaar_files:
                    name = (getattr(f, 'name', '') or '').lower()
                    ctype = getattr(f, 'content_type', '') or ''
                    if ctype == 'application/pdf' or name.endswith('.pdf'):
                        pdfs.append(f)
                    elif ctype.startswith('image/') or any(name.endswith(ext) for ext in ('.jpg','.jpeg','.png','.webp')):
                        imgs.append(f)
                folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
                if pdfs:
                    # Take the first/only PDF
                    f = pdfs[0]
                    up = drive_upload(f, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                elif imgs:
                    # Upload up to two images as front/back
                    # Reset back-side URL when replacing with images (avoid keeping stale back image)
                    inst.aadhaar_file_url_2 = ''
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    # First image
                    f1 = imgs[0]
                    ext1 = _pick_ext((getattr(f1, 'name', '') or '').lower())
                    up1 = drive_upload(f1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                    # Optional second image
                    if len(imgs) > 1:
                        f2 = imgs[1]
                        ext2 = _pick_ext((getattr(f2, 'name', '') or '').lower())
                        up2 = drive_upload(f2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2
            else:
                if app:
                    inst.aadhaar_file_url = app.aadhaar_file_url
                    # Preserve second url if present
                    inst.aadhaar_file_url_2 = getattr(app, 'aadhaar_file_url_2', '')
            # Status transitions
            is_new = app is None
            inst.save()
            from .models import ApplicationStatusHistory
            if is_new:
                inst.status = ResidentApplication.SUBMITTED
                inst.save(update_fields=['status'])
                ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Submitted by user')
            else:
                # If admin requested refill, mark as re-submitted; else treat as submitted
                if app.status == ResidentApplication.REFILL_REQUESTED:
                    inst.status = ResidentApplication.RESUBMITTED
                    inst.save(update_fields=['status'])
                    ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Re-submitted by user')
                elif app.status in (ResidentApplication.REJECTED, ResidentApplication.SUBMITTED, ResidentApplication.RESUBMITTED):
                    # Keep as submitted to indicate awaiting confirmation
                    inst.status = ResidentApplication.SUBMITTED
                    inst.save(update_fields=['status'])
                    ApplicationStatusHistory.objects.create(application=inst, status=inst.status, comment='Updated by user')

            # Notify PG Admins via in-app notification and email with a link to review/confirm
            try:
                admin_url = request.build_absolute_uri(reverse('pg_resident_applications'))
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
    return render(request, 'bookings/application_fill.html', {"form": form, "booking": booking, "app": app})
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
