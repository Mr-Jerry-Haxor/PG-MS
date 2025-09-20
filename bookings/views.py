import io
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.conf import settings
from django.utils import timezone

from pgadmin.models import PG
from accounts.models import Profile
from .models import Room, RoomShareStatus, Booking
from core.models import Notification
from core.audit import log
from django.core.mail import send_mail
from .forms import AadhaarForm, BookingRequestForm
from .application_forms import ResidentApplicationForm
from django.conf import settings
from core.drive import drive_upload
from django.db import IntegrityError
from django.urls import reverse
from django.utils.dateparse import parse_date


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
            return redirect('availability')
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
            aadhaar_pdf = request.FILES.get('aadhaar_pdf')
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            else:
                # keep existing
                if app:
                    inst.selfie_url = app.selfie_url
            if aadhaar_pdf:
                # Name with appropriate extension based on content type/filename
                name = getattr(aadhaar_pdf, 'name', '') or ''
                ctype = getattr(aadhaar_pdf, 'content_type', '') or ''
                if ctype == 'application/pdf' or name.lower().endswith('.pdf'):
                    fname = f"aadhaar_{request.user.id}.pdf"
                elif ctype.startswith('image/') or any(name.lower().endswith(ext) for ext in ('.jpg','.jpeg','.png','.webp')):
                    ext = '.jpg'
                    if name.lower().endswith('.png'): ext = '.png'
                    if name.lower().endswith('.webp'): ext = '.webp'
                    if name.lower().endswith('.jpeg') or name.lower().endswith('.jpg'): ext = '.jpg'
                    fname = f"aadhaar_{request.user.id}{ext}"
                else:
                    fname = f"aadhaar_{request.user.id}"
                up = drive_upload(aadhaar_pdf, fname, getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', ''))
                if up:
                    _fid, preview = up
                    inst.aadhaar_file_url = preview
            else:
                if app:
                    inst.aadhaar_file_url = app.aadhaar_file_url
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
