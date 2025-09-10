from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.utils.dateparse import parse_date
from django.db import IntegrityError
from django.db.models import Count, Q
try:
    from allauth.account.models import EmailAddress
except Exception:  # allauth not strictly required at import time
    EmailAddress = None

from .models import PG
from bookings.models import Room, RoomShareStatus, Booking
from bookings.models import ResidentApplication
from .forms import PGForm, RoomForm, ShareStatusForm
from core.models import Notification
from core.audit import log
from django.db.models import Exists, OuterRef


def _require_pg_admin(user):
    return hasattr(user, 'profile') and user.profile.is_pg_admin and user.profile.status == 'active'


def _admin_pgs(user):
    return PG.objects.filter(admins__user=user).order_by('name')


def _active_pg(request):
    """Resolve the active PG for a PG Admin user via ?pg=, session, or first available."""
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
def my_pg(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    # Allow switching if admin of multiple PGs
    pg = _active_pg(request) or PG.objects.filter(created_by_admin=request.user).first()
    if request.method == 'POST':
        form = PGForm(request.POST, instance=pg)
        if form.is_valid():
            pg = form.save(commit=False)
            if not pg.pk:
                pg.created_by_admin = request.user
            pg.save()
            messages.success(request, "PG saved.")
            return redirect('pg_my')
    else:
        form = PGForm(instance=pg)
    return render(request, 'pgadmin/my_pg.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})


@login_required
def rooms_list(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    if pg:
        rooms = (
            Room.objects.filter(pg=pg)
            .annotate(
                occupied_count=Count('shares', filter=Q(shares__status=RoomShareStatus.OCCUPIED)),
                reserved_count=Count('shares', filter=Q(shares__status=RoomShareStatus.RESERVED)),
                vacant_count=Count('shares', filter=Q(shares__status=RoomShareStatus.VACANT)),
            )
            .order_by('room_no')
        )
    else:
        rooms = []
    return render(request, 'pgadmin/rooms_list.html', {"pg": pg, "rooms": rooms, "pgs": list(_admin_pgs(request.user))})


@login_required
@transaction.atomic
def room_create(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    if request.method == 'POST':
        form = RoomForm(request.POST)
        if form.is_valid():
            room = form.save(commit=False)
            room.pg = pg
            room.save()
            # Ensure share rows exist
            for i in range(1, room.total_shares + 1):
                RoomShareStatus.objects.get_or_create(room=room, share_no=i)
            messages.success(request, "Room created.")
            return redirect('pg_rooms')
    else:
        form = RoomForm()
    return render(request, 'pgadmin/room_form.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user))})


@login_required
@transaction.atomic
def room_edit(request, pk):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    room = get_object_or_404(Room, pk=pk)
    if request.method == 'POST':
        form = RoomForm(request.POST, instance=room)
        if form.is_valid():
            room = form.save()
            # Sync shares count by adding missing ones (no deletion for safety)
            existing = room.shares.count()
            for i in range(existing + 1, room.total_shares + 1):
                RoomShareStatus.objects.get_or_create(room=room, share_no=i)
            messages.success(request, "Room updated.")
            return redirect('pg_rooms')
    else:
        form = RoomForm(instance=room)
    return render(request, 'pgadmin/room_form.html', {"form": form, "room": room})


@login_required
def room_shares(request, pk):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    room = get_object_or_404(Room, pk=pk)
    shares = room.shares.order_by('share_no')
    forms = [ShareStatusForm(prefix=f"s{rs.id}", instance=rs) for rs in shares]
    if request.method == 'POST':
        any_saved = False
        for rs in shares:
            prev_status = rs.status
            form = ShareStatusForm(request.POST, prefix=f"s{rs.id}", instance=rs)
            if form.is_valid():
                new_status = form.cleaned_data.get('status')
                # If moving from vacant to reserved/occupied, collect user details and create or link booking
                if prev_status == RoomShareStatus.VACANT and new_status in [RoomShareStatus.RESERVED, RoomShareStatus.OCCUPIED]:
                    email = request.POST.get(f"s{rs.id}-new-email", "").strip()
                    first_name = request.POST.get(f"s{rs.id}-new-first_name", "").strip()
                    last_name = request.POST.get(f"s{rs.id}-new-last_name", "").strip()
                    phone = request.POST.get(f"s{rs.id}-new-phone", "").strip()
                    joining_raw = request.POST.get(f"s{rs.id}-new-joining", "").strip()
                    joining_date = parse_date(joining_raw) if joining_raw else None

                    if not email:
                        messages.error(request, f"Share {rs.share_no}: Email is required to set {new_status}.")
                        continue

                    User = get_user_model()
                    user = User.objects.filter(email__iexact=email).first()
                    if not user:
                        # Create a lightweight account; user can later sign in via Google with same email
                        # Ensure unique username
                        base_username = email.split('@')[0][:20] or 'user'
                        username = base_username
                        suffix = 1
                        while User.objects.filter(username=username).exists():
                            suffix += 1
                            username = f"{base_username}{suffix}"
                        user = User(username=username, email=email, first_name=first_name or '', last_name=last_name or '')
                        user.set_unusable_password()
                        user.save()
                        created_user = True
                    else:
                        created_user = False
                        updated = False
                        if first_name and user.first_name != first_name:
                            user.first_name = first_name
                            updated = True
                        if last_name and user.last_name != last_name:
                            user.last_name = last_name
                            updated = True
                        if updated:
                            user.save(update_fields=['first_name', 'last_name'])

                    # Ensure an EmailAddress record exists for allauth and mark verified for seamless linking on future login
                    if EmailAddress is not None:
                        ea, _ = EmailAddress.objects.get_or_create(user=user, email=user.email, defaults={'verified': True, 'primary': True})
                        # If not primary/verified, make it so
                        to_update = []
                        if not ea.primary:
                            ea.primary = True
                            to_update.append('primary')
                        if not ea.verified:
                            ea.verified = True
                            to_update.append('verified')
                        if to_update:
                            ea.save(update_fields=to_update)

                    # Ensure profile exists and update phone
                    profile = getattr(user, 'profile', None)
                    if profile:
                        if phone and profile.phone != phone:
                            profile.phone = phone
                            profile.save(update_fields=['phone'])
                    any_saved = True

                    # Create the booking according to status
                    try:
                        booking_status = Booking.APPROVED if new_status == RoomShareStatus.OCCUPIED else Booking.PENDING
                        booking = Booking(
                            user=user,
                            room=room,
                            share_no=rs.share_no,
                            status=booking_status,
                            joining_date=joining_date,
                        )
                        if booking_status == Booking.APPROVED:
                            booking.start_date = joining_date or timezone.now().date()
                        booking.save()
                    except IntegrityError:
                        messages.error(request, f"Share {rs.share_no}: Could not create booking. User may already have an active booking in this PG.")
                        # Skip saving status change for this share
                        continue

                    # Save the share status change after booking created
                    form.save()
                    # Feedback
                    if created_user:
                        messages.success(request, f"Share {rs.share_no}: User created: {user.email}, booking: {booking.get_status_display()}.")
                    else:
                        messages.success(request, f"Share {rs.share_no}: User linked: {user.email}, booking: {booking.get_status_display()}.")
                else:
                    # Normal save and inline occupant updates when already occupied
                    form.save()
                    any_saved = True
                    occ = (
                        Booking.objects.filter(room=room, share_no=rs.share_no, status=Booking.APPROVED)
                        .select_related('user', 'user__profile')
                        .order_by('-created_at')
                        .first()
                    )
                    if occ:
                        phone_key = f"s{rs.id}-phone"
                        leaving_key = f"s{rs.id}-leaving"
                        phone_val = request.POST.get(phone_key)
                        leaving_val = request.POST.get(leaving_key)
                        if phone_val is not None and hasattr(occ.user, 'profile'):
                            if occ.user.profile.phone != phone_val:
                                occ.user.profile.phone = phone_val
                                occ.user.profile.save(update_fields=['phone'])
                        if leaving_val is not None:
                            new_date = parse_date(leaving_val) if leaving_val else None
                            if occ.leaving_date != new_date:
                                occ.leaving_date = new_date
                                occ.save(update_fields=['leaving_date'])
        if any_saved:
            messages.success(request, "Changes saved.")
            return redirect('pg_room_shares', pk=room.id)
    # Build (share, form, occupant) where occupant is latest approved booking for this share
    rs_forms = []
    for rs, form in zip(shares, forms):
        occupant = None
        if rs.status == RoomShareStatus.OCCUPIED:
            occupant = (
                Booking.objects.filter(room=room, share_no=rs.share_no, status=Booking.APPROVED)
                .select_related('user', 'room', 'user__profile')
                .order_by('-created_at')
                .first()
            )
        # Determine application status for occupant booking
        app_exists = False
        if occupant:
            from bookings.models import ResidentApplication
            app_exists = ResidentApplication.objects.filter(booking=occupant).exists()
        rs_forms.append((rs, form, occupant, app_exists))
    return render(request, 'pgadmin/room_shares.html', {"room": room, "forms": forms, "shares": shares, "rs_forms": rs_forms})


# --- Booking approvals (PG Admin) ---

@login_required
def bookings_pending(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    pending = []
    if pg:
        pending = (
            Booking.objects.filter(status=Booking.PENDING, room__pg=pg)
            .select_related('user', 'room')
            .annotate(has_application=Exists(ResidentApplication.objects.filter(booking_id=OuterRef('pk'))))
        )
    return render(request, 'pgadmin/bookings_pending.html', {"pg": pg, "bookings": pending, "pgs": list(_admin_pgs(request.user))})


@login_required
@transaction.atomic
def booking_approve(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.PENDING)
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    booking.status = Booking.APPROVED
    booking.start_date = timezone.now().date()
    booking.save()
    share.status = RoomShareStatus.OCCUPIED
    share.save(update_fields=['status'])
    log(request.user, 'booking_approved', 'Booking', booking.id, f"Approved for room {booking.room.room_no} share {booking.share_no}")
    # Notify user
    Notification.objects.create(user=booking.user, title="Booking approved", message=f"Your booking for {booking.room} share {booking.share_no} was approved.")
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[booking.id]))
        send_mail(
            subject="PG-MS: Booking Approved",
            message=f"Your booking for {booking.room} share {booking.share_no} was approved.\nPlease complete your resident application here: {link}",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.success(request, "Booking approved and user notified.")
    return redirect('pg_bookings_pending')


@login_required
@transaction.atomic
def booking_reject(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.PENDING)
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    booking.status = Booking.REJECTED
    booking.save(update_fields=['status'])
    # On rejection, revert share to appropriate availability state.
    # Rule: if share.vacant_from is in the future -> keep VACANT_FROM (future scheduled vacancy)
    # else (date is past or today OR no date) -> mark as VACANT now.
    today = timezone.now().date()
    update_fields = ['status']
    if share.vacant_from and share.vacant_from > today:
        share.status = RoomShareStatus.VACANT_FROM
        # keep vacant_from date
    else:
        share.status = RoomShareStatus.VACANT
        if share.vacant_from:
            # Clear stale date once it's already vacant
            share.vacant_from = None
            update_fields.append('vacant_from')
    share.save(update_fields=update_fields)
    log(request.user, 'booking_rejected', 'Booking', booking.id, f"Rejected for room {booking.room.room_no} share {booking.share_no}")
    Notification.objects.create(user=booking.user, title="Booking rejected", message=f"Your booking for {booking.room} share {booking.share_no} was rejected.")
    try:
        send_mail(
            subject="PG-MS: Booking Rejected",
            message=f"Your booking for {booking.room} share {booking.share_no} was rejected.",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.info(request, "Booking rejected.")
    return redirect('pg_bookings_pending')


@login_required
def leaving_requests(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    requests_qs = Booking.objects.filter(room__pg=pg, leaving_date__isnull=False, status=Booking.APPROVED).select_related('user', 'room') if pg else []
    today = timezone.now().date() if pg else None
    return render(request, 'pgadmin/leaving_requests.html', {"pg": pg, "bookings": requests_qs, "pgs": list(_admin_pgs(request.user)), "today": today})


@login_required
def application_email_send(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id)
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[booking.id]))
        send_mail(
            subject="PG-MS: Complete Your Resident Application",
            message=f"Please complete your resident application here: {link}",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=True,
        )
        messages.success(request, f"Application link sent to {booking.user.email}.")
    except Exception:
        messages.error(request, "Could not send email.")
    # Redirect back to applications list if invoked from there
    if request.GET.get('from') == 'applications':
        return redirect('pg_resident_applications')
    return redirect('pg_bookings_pending')


@login_required
@transaction.atomic
def leaving_confirm(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.APPROVED)
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    # Mark leaving as confirmed (store timestamp/date)
    today = timezone.now().date()
    updated_fields = []
    if not booking.leaving_confirmed_date:
        booking.leaving_confirmed_date = today
        updated_fields.append('leaving_confirmed_date')
    # If leaving date already reached or past, free immediately; else keep occupied until date
    if booking.leaving_date:
        share.status = RoomShareStatus.VACANT_FROM
        share.vacant_from = booking.leaving_date
        share.save(update_fields=['status','vacant_from'])
    if updated_fields:
        booking.save(update_fields=updated_fields)
    try:
        booking.user.profile.status = 'inactive'
        booking.user.profile.save(update_fields=['status'])
    except Exception:
        pass
    log(request.user, 'leaving_confirmed', 'Booking', booking.id, f"Leaving confirmed; booking closed for room {booking.room.room_no} share {booking.share_no}")
    if share.status == RoomShareStatus.VACANT:
        messages.success(request, f"Leaving confirmed and share freed for room {booking.room.room_no}.")
    else:
        messages.success(request, f"Leaving confirmed for room {booking.room.room_no}. Share will free on {booking.leaving_date}.")
    return redirect('pg_leaving_requests')
from django.shortcuts import render

# Create your views here.


@login_required
def resident_applications(request):
    # Show all approved bookings for this PG with application status/actions
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    bookings = []
    if pg:
        bookings = (
            Booking.objects.filter(room__pg=pg, status=Booking.APPROVED)
            .select_related('user', 'room', 'application')
            .order_by('room__room_no', 'share_no')
        )
    submitted_count = bookings.filter(application__isnull=False).count() if bookings else 0
    pending_count = bookings.filter(application__isnull=True).count() if bookings else 0
    return render(
        request,
        'pgadmin/resident_applications.html',
        {
            "pg": pg,
            "bookings": bookings,
            "pgs": list(_admin_pgs(request.user)),
            "submitted_count": submitted_count,
            "pending_count": pending_count,
        },
    )
