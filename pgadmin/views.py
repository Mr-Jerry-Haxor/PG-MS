from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, Min, OuterRef, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

try:
    from allauth.account.models import EmailAddress
except Exception:  # allauth not strictly required at import time
    EmailAddress = None

from bookings.models import Booking, ResidentApplication, Room, RoomShareStatus
from core.audit import log
from core.drive import drive_delete
from core.models import Notification
from finance.models import Fees
from .forms import PGForm, RoomForm, ShareStatusForm
from .models import PG, PGAdmin

@login_required
def booking_joining_update(request, booking_id):
    from bookings.models import Booking
    booking = get_object_or_404(Booking, pk=booking_id)
    # Authorization: must be a PG Admin and admin of the booking's PG
    u = request.user
    if not _require_pg_admin(u) or not _admin_pgs(u).filter(id=getattr(booking, 'pg_id', None)).exists():
        messages.error(request, 'PG Admin access required for this PG.')
        return redirect('pg_resident_applications')
    if request.method != 'POST':
        messages.error(request, 'Unsupported request method.')
        return redirect('pg_resident_applications')
    date_str = (request.POST.get('joining_date') or '').strip()
    if not date_str:
        messages.error(request, 'Joining date is required.')
        return redirect(request.META.get('HTTP_REFERER') or 'pg_resident_applications')
    dt = parse_date(date_str)
    if not dt:
        messages.error(request, 'Invalid date format. Use YYYY-MM-DD.')
        return redirect(request.META.get('HTTP_REFERER') or 'pg_resident_applications')
    try:
        booking.joining_date = dt
        booking.save(update_fields=['joining_date'])
        messages.success(request, f'Joining date updated to {dt}.')
    except Exception as e:
        messages.error(request, f'Could not update joining date: {e}')
    return redirect(request.META.get('HTTP_REFERER') or 'pg_resident_applications')


@login_required
@transaction.atomic
def booking_leave_direct(request, booking_id: int) -> JsonResponse:
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Invalid request method.'}, status=405)
    if not _require_pg_admin(request.user):
        return JsonResponse({'ok': False, 'error': 'PG Admin access required.'}, status=403)
    booking = get_object_or_404(Booking.objects.select_for_update(), pk=booking_id, status=Booking.APPROVED)
    pg_id = getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None)
    if not _admin_pgs(request.user).filter(id=pg_id).exists():
        return JsonResponse({'ok': False, 'error': 'PG Admin access required for this PG.'}, status=403)
    date_str = (request.POST.get('leaving_date') or '').strip()
    if not date_str:
        return JsonResponse({'ok': False, 'error': 'Leaving date is required.'}, status=400)
    leaving_date = parse_date(date_str)
    if not leaving_date:
        return JsonResponse({'ok': False, 'error': 'Invalid leaving date.'}, status=400)
    min_allowed = booking.joining_date or booking.start_date
    if min_allowed and leaving_date < min_allowed:
        return JsonResponse({'ok': False, 'error': f'Leaving date must be on or after {min_allowed}.'}, status=400)

    share = get_object_or_404(
        RoomShareStatus.objects.select_for_update(),
        room=booking.room,
        share_no=booking.share_no,
    )

    previous_status = share.status

    update_fields = ['leaving_date']
    booking.leaving_date = leaving_date
    if booking.leaving_confirmed_date:
        booking.leaving_confirmed_date = None
        update_fields.append('leaving_confirmed_date')
    booking.save(update_fields=update_fields)

    share.status = RoomShareStatus.VACANT_FROM
    share.vacant_from = leaving_date
    share.save(update_fields=['status', 'vacant_from'])

    room_counts = _room_share_counts(booking.room)

    log(
        request.user,
        'booking_leave_requested',
        'Booking',
        booking.id,
        f"Leave requested for room {booking.room.room_no} bed {booking.share_no} on {leaving_date}",
    )

    return JsonResponse({
        'ok': True,
        'action': 'booking_leave_requested',
        'booking_id': booking.id,
        'room_id': booking.room_id,
        'leaving_date': leaving_date.isoformat(),
        'share_status': share.status,
        'vacant_from': share.vacant_from.isoformat() if share.vacant_from else '',
        'previous_status': previous_status,
        'room_counts': room_counts,
        'message': 'Leave request recorded and pending confirmation.',
        'leave_message': f'Leave requested for {leaving_date.isoformat()} (awaiting confirmation).',
    })


@login_required
@transaction.atomic
def booking_swap_room(request, booking_id: int) -> JsonResponse:
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Invalid request method.'}, status=405)
    if not _require_pg_admin(request.user):
        return JsonResponse({'ok': False, 'error': 'PG Admin access required.'}, status=403)
    booking = get_object_or_404(Booking.objects.select_for_update(), pk=booking_id, status=Booking.APPROVED)
    pg_id = getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None)
    if not _admin_pgs(request.user).filter(id=pg_id).exists():
        return JsonResponse({'ok': False, 'error': 'PG Admin access required for this PG.'}, status=403)

    try:
        room_id = int(request.POST.get('room_id'))
        share_no = int(request.POST.get('share_no'))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Room and bed selections are required.'}, status=400)

    if room_id == booking.room_id and share_no == booking.share_no:
        return JsonResponse({'ok': False, 'error': 'Select a different room or bed to swap.'}, status=400)

    new_room = get_object_or_404(Room.objects.select_for_update(), pk=room_id, pg_id=pg_id)
    new_share = get_object_or_404(RoomShareStatus.objects.select_for_update(), room=new_room, share_no=share_no)
    if new_share.status != RoomShareStatus.VACANT:
        return JsonResponse({'ok': False, 'error': 'Selected bed is no longer vacant.'}, status=400)

    old_room = booking.room
    old_share = get_object_or_404(RoomShareStatus.objects.select_for_update(), room=old_room, share_no=booking.share_no)

    # Free old share
    old_share.status = RoomShareStatus.VACANT
    old_share.vacant_from = None
    old_share.save(update_fields=['status', 'vacant_from'])

    # Occupy new share
    new_share.status = RoomShareStatus.OCCUPIED
    new_share.vacant_from = None
    new_share.save(update_fields=['status', 'vacant_from'])

    # Update booking
    booking.room = new_room
    booking.share_no = share_no
    booking.save(update_fields=['room', 'pg', 'share_no'])

    app = getattr(booking, 'application', None)
    if app and app.room_id != new_room.id:
        app.room = new_room
        app.save(update_fields=['room'])

    log(request.user, 'booking_swap', 'Booking', booking.id, f"Swapped to room {new_room.room_no} bed {share_no}")

    old_share.refresh_from_db()
    new_share.refresh_from_db()
    today = timezone.now().date()

    old_card_html = render_to_string(
        'pgadmin/_tenant_share_card.html',
        {'sd': _build_share_detail(old_room, old_share), 'today': today},
        request=request,
    )
    new_card_html = render_to_string(
        'pgadmin/_tenant_share_card.html',
        {'sd': _build_share_detail(new_room, new_share), 'today': today},
        request=request,
    )

    return JsonResponse({
        'ok': True,
        'action': 'booking_swap',
        'booking_id': booking.id,
        'old_room_id': old_room.id,
        'new_room_id': new_room.id,
        'old_share_no': old_share.share_no,
        'new_share_no': share_no,
        'old_card_html': old_card_html,
        'new_card_html': new_card_html,
        'old_room_counts': _room_share_counts(old_room),
        'new_room_counts': _room_share_counts(new_room),
    })


@login_required
def booking_swap_rooms_api(request, booking_id: int) -> JsonResponse:
    if request.method != 'GET':
        return JsonResponse({'ok': False, 'error': 'Invalid request method.'}, status=405)
    if not _require_pg_admin(request.user):
        return JsonResponse({'ok': False, 'error': 'PG Admin access required.'}, status=403)
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.APPROVED)
    pg_id = getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None)
    if not _admin_pgs(request.user).filter(id=pg_id).exists():
        return JsonResponse({'ok': False, 'error': 'PG Admin access required for this PG.'}, status=403)

    rooms = (
        Room.objects.filter(pg_id=pg_id)
        .annotate(vacant_count=Count('shares', filter=Q(shares__status=RoomShareStatus.VACANT)))
        .order_by('room_no')
    )

    room_data = [
        {
            'id': room.id,
            'room_no': room.room_no,
            'vacant_count': room.vacant_count,
            'total_beds': room.total_shares,
        }
        for room in rooms
        if room.vacant_count > 0
    ]

    return JsonResponse({
        'ok': True,
        'rooms': room_data,
        'current_room_id': booking.room_id,
        'current_share_no': booking.share_no,
    })


@login_required
def booking_swap_shares_api(request, booking_id: int, room_id: int) -> JsonResponse:
    if request.method != 'GET':
        return JsonResponse({'ok': False, 'error': 'Invalid request method.'}, status=405)
    if not _require_pg_admin(request.user):
        return JsonResponse({'ok': False, 'error': 'PG Admin access required.'}, status=403)
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.APPROVED)
    pg_id = getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None)
    if not _admin_pgs(request.user).filter(id=pg_id).exists():
        return JsonResponse({'ok': False, 'error': 'PG Admin access required for this PG.'}, status=403)

    room = get_object_or_404(Room, pk=room_id, pg_id=pg_id)
    shares = RoomShareStatus.objects.filter(room=room, status=RoomShareStatus.VACANT).order_by('share_no')

    data = [
        {
            'share_no': share.share_no,
            'status': share.status,
        }
        for share in shares
    ]

    return JsonResponse({'ok': True, 'room_id': room.id, 'shares': data})
def _require_pg_admin(user):
    # Superusers and website admins can access PG-admin area
    if getattr(user, 'is_superuser', False):
        return True
    if hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False):
        return True
    # Fall back to explicit PGAdmin membership (do not rely solely on profile flags)
    try:
        if PGAdmin.objects.filter(user=user).exists():
            return True
    except Exception:
        pass
    return hasattr(user, 'profile') and getattr(user.profile, 'is_pg_admin', False) and getattr(user.profile, 'status', 'active') == 'active'


def _admin_pgs(user):
    # Superusers and website admins see all PGs
    if getattr(user, 'is_superuser', False) or (hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False)):
        return PG.objects.all().order_by('name')
    return PG.objects.filter(admins__user=user).order_by('name')


def _active_pg(request):
    """Resolve the active PG for a PG Admin user via ?pg=, session, or first available."""
    qs = _admin_pgs(request.user)
    pg = None
    pg_id = request.GET.get('pg') or request.session.get('active_pg_id')
    if pg_id:
        # Only allow selecting PGs within authorized set
        pg = qs.filter(id=pg_id).first()
    if not pg:
        pg = qs.first()
    if pg:
        request.session['active_pg_id'] = pg.id
    return pg


def _booking_drive_urls(booking: Booking) -> set[str]:
    urls: set[str] = set()
    app = getattr(booking, 'application', None)
    if app:
        urls.update([u for u in [getattr(app, 'selfie_url', ''), getattr(app, 'aadhaar_file_url', ''), getattr(app, 'aadhaar_file_url_2', '')] if u])
    profile = getattr(getattr(booking, 'user', None), 'profile', None)
    if profile:
        urls.update([u for u in [getattr(profile, 'selfie_url', ''), getattr(profile, 'aadhaar_file_url', '')] if u])
    return urls


def _build_share_detail(room: Room, share: RoomShareStatus) -> dict:
    booking = None
    occupant = None
    application = None
    if share.status in [RoomShareStatus.OCCUPIED, RoomShareStatus.VACANT_FROM]:
        booking = (
            Booking.objects.filter(room=room, share_no=share.share_no, status=Booking.APPROVED)
            .select_related('user', 'user__profile')
            .order_by('-created_at')
            .first()
        )
    elif share.status == RoomShareStatus.RESERVED:
        booking = (
            Booking.objects.filter(room=room, share_no=share.share_no, status=Booking.PENDING)
            .select_related('user', 'user__profile')
            .order_by('-created_at')
            .first()
        )
    if booking is not None:
        occupant = getattr(booking, 'user', None)
        try:
            application = booking.application
        except Exception:
            application = None
    return {
        'share': share,
        'booking': booking,
        'occupant': occupant,
        'application': application,
        'is_pending': bool(booking and booking.status == Booking.PENDING),
    }


def _room_share_counts(room: Room) -> dict:
    counts = {
        'total': 0,
        'vacant': 0,
        'occupied': 0,
        'reserved': 0,
        'leaving': 0,
    }
    for status in RoomShareStatus.objects.filter(room=room).values_list('status', flat=True):
        counts['total'] += 1
        if status == RoomShareStatus.VACANT:
            counts['vacant'] += 1
        elif status == RoomShareStatus.OCCUPIED:
            counts['occupied'] += 1
        elif status == RoomShareStatus.RESERVED:
            counts['reserved'] += 1
        elif status == RoomShareStatus.VACANT_FROM:
            counts['leaving'] += 1
            counts['occupied'] += 1
    return counts


def _room_share_breakdown(room: Room) -> dict:
    counts = {
        'total': room.total_shares,
        'vacant': 0,
        'occupied': 0,
        'reserved': 0,
        'leaving': 0,
    }
    for status in room.shares.values_list('status', flat=True):
        if status == RoomShareStatus.VACANT:
            counts['vacant'] += 1
        elif status == RoomShareStatus.OCCUPIED:
            counts['occupied'] += 1
        elif status == RoomShareStatus.RESERVED:
            counts['reserved'] += 1
        elif status == RoomShareStatus.VACANT_FROM:
            counts['leaving'] += 1
            counts['occupied'] += 1
    counts['non_vacant'] = counts['occupied'] + counts['reserved']
    return counts


def _shrink_room_shares(room: Room, new_total: int) -> tuple[bool, dict | str]:
    shares = list(RoomShareStatus.objects.select_for_update().filter(room=room).order_by('share_no'))
    old_total = len(shares)
    remove_needed = old_total - new_total
    if remove_needed <= 0:
        return True, {'removed_numbers': [], 'reassignments': []}

    vacants = [s for s in shares if s.status == RoomShareStatus.VACANT]
    non_vacant = [s for s in shares if s.status != RoomShareStatus.VACANT]

    if remove_needed > len(vacants):
        shortfall = remove_needed - len(vacants)
        return False, (
            f"Need {remove_needed} vacant bed(s) to shrink, but only {len(vacants)} vacant now. "
            f"Free up {shortfall} more bed(s) or move residents before reducing."
        )

    if len(non_vacant) > new_total:
        overload = len(non_vacant) - new_total
        return False, (
            f"Cannot reduce to {new_total} bed(s) while {len(non_vacant)} bed(s) are occupied/reserved. "
            f"Vacate or relocate {overload} bed(s) first."
        )

    ordered = non_vacant + vacants
    keepers = ordered[:new_total]
    removals = ordered[new_total:]

    # Defensive: removals should be vacant and booking-free
    for share in removals:
        if share.status != RoomShareStatus.VACANT:
            return False, "Unexpected occupied bed selected for removal. Please refresh and retry."
        if Booking.objects.filter(
            room=room,
            share_no=share.share_no,
            status__in=[Booking.PENDING, Booking.APPROVED],
        ).exists():
            return False, "A pending or approved booking is still linked to a bed slated for removal."

    removed_numbers: list[int] = [share.share_no for share in removals]
    for share in removals:
        share.delete()

    target_map = {share.pk: idx + 1 for idx, share in enumerate(keepers)}
    original_numbers = {share.pk: share.share_no for share in keepers}
    change_plan = [
        (share, original_numbers[share.pk], target_map[share.pk])
        for share in keepers
        if original_numbers[share.pk] != target_map[share.pk]
    ]

    temp_base = old_total + 10
    for offset, (share, _original, _target) in enumerate(change_plan, start=1):
        share.share_no = temp_base + offset
        share.save(update_fields=['share_no'])

    for _share, original, target in change_plan:
        Booking.objects.filter(room=room, share_no=original).update(share_no=target)

    for share, _original, target in change_plan:
        share.share_no = target
        share.save(update_fields=['share_no'])

    reassigned = [
        {'from': original, 'to': target, 'status': share.status}
        for share, original, target in change_plan
    ]

    return True, {'removed_numbers': removed_numbers, 'reassignments': reassigned}


def _cleanup_booking_after_leave(booking: Booking, actor=None, origin: str = 'manual') -> dict:
    """Delete booking/application artifacts once a resident has left."""
    share = RoomShareStatus.objects.filter(room=booking.room, share_no=booking.share_no).first()
    share_updated = False
    if share:
        share.status = RoomShareStatus.VACANT
        share.vacant_from = None
        share.save(update_fields=['status', 'vacant_from'])
        share_updated = True

    profile = getattr(booking.user, 'profile', None)
    profile_updates: list[str] = []
    if profile and getattr(profile, 'is_pg_user', True):
        profile.is_pg_user = False
        profile_updates.append('is_pg_user')

    drive_urls = _booking_drive_urls(booking)
    deleted_urls: list[str] = []
    failed_urls: list[str] = []
    for url in drive_urls:
        success = drive_delete(url)
        if success:
            deleted_urls.append(url)
        else:
            failed_urls.append(url)

    if profile:
        if profile.selfie_url and profile.selfie_url in drive_urls:
            profile.selfie_url = ''
            profile_updates.append('selfie_url')
        if profile.aadhaar_file_url and profile.aadhaar_file_url in drive_urls:
            profile.aadhaar_file_url = ''
            profile_updates.append('aadhaar_file_url')
        if profile_updates:
            # Remove duplicates while preserving order
            seen = set()
            ordered_updates = []
            for field in profile_updates:
                if field not in seen:
                    ordered_updates.append(field)
                    seen.add(field)
            profile.save(update_fields=ordered_updates)
            profile_updates = ordered_updates

    app = getattr(booking, 'application', None)
    app_id = getattr(app, 'id', None)
    booking_id = booking.id
    user_id = booking.user_id
    room_no = getattr(getattr(booking, 'room', None), 'room_no', '')
    share_no = booking.share_no

    booking.delete()

    meta = {
        'origin': origin,
        'files_attempted': len(drive_urls),
        'files_deleted': len(deleted_urls),
        'files_failed': len(failed_urls),
        'application_id': app_id,
        'user_id': user_id,
        'share_updated': share_updated,
    }
    log(actor, 'leave_cleanup_deleted', 'Booking', booking_id, message=f"Leave cleanup ({origin}) for room {room_no} bed {share_no}", meta=meta)

    return {
        'share_updated': share_updated,
        'deleted_files': len(deleted_urls),
        'failed_files': len(failed_urls),
        'profile_updates': profile_updates,
        'files_attempted': len(drive_urls),
    }


@login_required
def my_pg(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    # Allow switching if admin of multiple PGs
    pg = _active_pg(request) or PG.objects.filter(created_by_admin=request.user).first()
    # Auto-convert past-dated VACANT_FROM shares to VACANT on dashboard load
    if pg:
        try:
            today = timezone.now().date()
            qs_cleanup = RoomShareStatus.objects.filter(
                room__pg=pg,
                status=RoomShareStatus.VACANT_FROM,
                vacant_from__isnull=False,
                vacant_from__lt=today,
            )
            if qs_cleanup.exists():
                qs_cleanup.update(status=RoomShareStatus.VACANT, vacant_from=None)
        except Exception:
            # Non-blocking: ignore failures in cleanup
            pass
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
    quick_url = None
    if pg:
        try:
            quick_url = request.build_absolute_uri(reverse('pg_quick_booking', kwargs={'pgslug': pg.slug}))
        except Exception:
            quick_url = None
    fees = list(Fees.objects.filter(pg=pg)) if pg else []
    return render(request, 'pgadmin/my_pg.html', {"form": form, "pg": pg, "pgs": list(_admin_pgs(request.user)), "quick_booking_url": quick_url, "fees": fees})


@login_required
def tenants(request):
    """My PG Tenants view: shows rooms in ascending order and per-bed occupancy details.
    If admin has multiple PGs, shows a PG selector; otherwise directly shows the active PG.
    """
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    # Allow switching PG via ?pg= param (already handled by _active_pg)
    pg = _active_pg(request)
    rooms = []
    if pg:
        rooms = (
            Room.objects.filter(pg=pg)
            .prefetch_related('shares', 'bookings__user', 'bookings__user__profile')
            .order_by('room_no')
        )
    # Build a derived structure for template: per room -> counts and bed details
    data = []
    for room in rooms:
        shares = list(room.shares.all())
        # Counts
        vac = sum(1 for s in shares if s.status == RoomShareStatus.VACANT)
        occ = sum(1 for s in shares if s.status in [RoomShareStatus.OCCUPIED, RoomShareStatus.VACANT_FROM])
        res = sum(1 for s in shares if s.status == RoomShareStatus.RESERVED)
        leaving = sum(1 for s in shares if s.status == RoomShareStatus.VACANT_FROM)
        # For each share, find latest approved booking for occupant details
        share_details = [_build_share_detail(room, s) for s in sorted(shares, key=lambda x: x.share_no)]
        data.append({
            'room': room,
            'counts': {
                'vacant': vac,
                'occupied': occ,
                'reserved': res,
                'leaving': leaving,
            },
            'shares': share_details,
        })

    ctx = {
        'pg': pg,
        'pgs': list(_admin_pgs(request.user)),
        'rooms': data,
        'today': timezone.now().date(),
    }
    return render(request, 'pgadmin/tenants.html', ctx)


@login_required
def quick_booking_qr_pdf(request, pg_id: int):
    """Generate a single-page PDF containing a QR code for the PG's quick booking URL."""
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = get_object_or_404(PG, pk=pg_id)
    # Ensure user is admin of this PG
    if not _admin_pgs(request.user).filter(id=pg.id).exists():
        messages.error(request, 'PG Admin access required for this PG.')
        return redirect('pg_my')

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
        from reportlab.graphics.barcode import qr as rl_qr
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics import renderPDF
        from reportlab.pdfbase.pdfmetrics import stringWidth
        import io as _io
    except Exception as e:
        return HttpResponse(f"PDF generation dependencies missing: {e}", status=500)

    quick_url = request.build_absolute_uri(reverse('pg_quick_booking', kwargs={'pgslug': pg.slug}))

    buf = _io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Header: PG name and label text centered at top
    top_margin = 12 * mm
    side_margin = 10 * mm
    footer_space = 10 * mm
    # Draw title (PG name) with auto-scaling if too long
    title_font = "Helvetica-Bold"
    title_size = 18.0
    max_title_width = width - 2 * side_margin
    while title_size > 10 and stringWidth(pg.name, title_font, title_size) > max_title_width:
        title_size -= 1
    c.setFont(title_font, title_size)
    title_y = height - top_margin
    c.drawCentredString(width / 2.0, title_y, pg.name)

    # Subtitle: Quick Booking
    subtitle_font = "Helvetica"
    subtitle_size = 12.0
    max_subtitle_width = width - 2 * side_margin
    while subtitle_size > 8 and stringWidth("Quick Booking", subtitle_font, subtitle_size) > max_subtitle_width:
        subtitle_size -= 1
    c.setFont(subtitle_font, subtitle_size)
    subtitle_y = title_y - 8 * mm
    c.drawCentredString(width / 2.0, subtitle_y, "Quick Booking")

    # Reserve header space and compute available area for QR
    header_space = max((height - subtitle_y) + 8 * mm, 30 * mm)  # ensure some space under subtitle
    available_height = height - header_space - footer_space

    # Create a large QR under header, within margins, centered
    qr_widget = rl_qr.QrCodeWidget(quick_url)
    bounds = qr_widget.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    size = min(width - 2 * side_margin, available_height)
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(qr_widget)
    x = (width - size) / 2.0
    y = footer_space + (available_height - size) / 2.0
    renderPDF.draw(d, c, x, y)

    c.showPage()
    c.save()
    pdf = buf.getvalue()
    buf.close()
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{pg.name}_quick_booking_qr.pdf"'
    return resp


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
                occupied_count=Count('shares', filter=Q(shares__status__in=[RoomShareStatus.OCCUPIED, RoomShareStatus.VACANT_FROM])),
                reserved_count=Count('shares', filter=Q(shares__status=RoomShareStatus.RESERVED)),
                vacant_count=Count('shares', filter=Q(shares__status=RoomShareStatus.VACANT)),
                leaving_count=Count('shares', filter=Q(shares__status=RoomShareStatus.VACANT_FROM)),
                next_vacant_from=Min('shares__vacant_from', filter=Q(shares__status=RoomShareStatus.VACANT_FROM)),
            )
            .prefetch_related(
                Prefetch(
                    'shares',
                    queryset=RoomShareStatus.objects.filter(status=RoomShareStatus.VACANT_FROM).only('id','share_no','vacant_from').order_by('vacant_from'),
                    to_attr='vacant_from_shares',
                )
            )
            .order_by('room_no')
        )
    # Apply optional filter by room bed status
        only = (request.GET.get('filter') or '').strip().lower()
        if only == 'vacant':
            rooms = rooms.filter(vacant_count__gt=0)
        elif only == 'leaving':
            rooms = rooms.filter(leaving_count__gt=0)
        elif only == 'reserved':
            rooms = rooms.filter(reserved_count__gt=0)
        elif only == 'occupied':
            rooms = rooms.filter(occupied_count__gt=0)
    else:
        rooms = []
    return render(request, 'pgadmin/rooms_list.html', {"pg": pg, "rooms": rooms, "pgs": list(_admin_pgs(request.user)), "active_filter": (request.GET.get('filter') or '')})


@login_required
def vehicle_search(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    compiled_results: list[dict] = []
    total_results = 0
    if pg:
        today = timezone.localdate()
        qs = (
            ResidentApplication.objects.filter(pg=pg, has_vehicle=True)
            .filter(booking__isnull=False)
            .filter(Q(booking__leaving_date__isnull=True) | Q(booking__leaving_date__gt=today))
            .select_related('booking', 'booking__room', 'booking__user', 'booking__user__profile')
            .order_by('name', 'vehicle_number')
        )
        for app in qs:
            booking = getattr(app, 'booking', None)
            user = getattr(booking, 'user', None) if booking else None
            profile = getattr(user, 'profile', None) if user else None
            if app.name:
                resident_name = app.name
            elif user:
                full_name = getattr(user, 'get_full_name', lambda: '')()
                resident_name = (full_name or '').strip() or getattr(user, 'email', '')
            else:
                resident_name = ''
            room = getattr(booking, 'room', None) if booking else None
            contact_phone = app.phone or (getattr(profile, 'phone', '') if profile else '')
            compiled_results.append({
                'app': app,
                'resident_name': resident_name,
                'room_no': getattr(room, 'room_no', ''),
                'contact_phone': contact_phone,
                'vehicle_number': app.vehicle_number or '',
                'vehicle_model': app.vehicle_model or '',
                'search_blob': ' '.join(filter(None, [
                    resident_name,
                    getattr(user, 'email', '') if user else '',
                    contact_phone,
                    getattr(room, 'room_no', '') if room else '',
                    str(getattr(booking, 'share_no', '')) if booking else '',
                    app.vehicle_number or '',
                    app.vehicle_model or '',
                ])).lower(),
            })
        total_results = len(compiled_results)
    ctx = {
        'pg': pg,
        'pgs': list(_admin_pgs(request.user)),
        'compiled_results': compiled_results,
        'total_results': total_results,
    }
    return render(request, 'pgadmin/vehicle_search.html', ctx)


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
            # Ensure bed rows exist
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
    if not _admin_pgs(request.user).filter(id=room.pg_id).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    share_stats = _room_share_breakdown(room)
    if request.method == 'POST':
        old_total = room.total_shares
        form = RoomForm(request.POST, instance=room)
        if form.is_valid():
            new_total = form.cleaned_data['total_shares']
            room_obj = form.save(commit=False)

            if new_total == old_total:
                room_obj.save()
                messages.success(request, "Room details updated.")
                return redirect('pg_rooms')

            if new_total > old_total:
                room_obj.save()
                for share_no in range(old_total + 1, new_total + 1):
                    RoomShareStatus.objects.get_or_create(
                        room=room_obj,
                        share_no=share_no,
                        defaults={'status': RoomShareStatus.VACANT},
                    )
                added = new_total - old_total
                log(request.user, 'room_size_increase', 'Room', room_obj.id, f'Beds increased by {added}.')
                messages.success(request, f"Room updated. Added {added} new vacant bed{'s' if added != 1 else ''}.")
                return redirect('pg_rooms')

            success, payload = _shrink_room_shares(room, new_total)
            if success:
                room_obj.save()
                removed = payload.get('removed_numbers', [])
                reassigned = payload.get('reassignments', [])
                log_msg = f"Beds reduced to {new_total}. Removed {len(removed)} bed(s)."
                if reassigned:
                    moves = ', '.join(f"{item['from']}→{item['to']}" for item in reassigned)
                    log_msg += f" Reassigned beds: {moves}."
                log(request.user, 'room_size_decrease', 'Room', room_obj.id, log_msg)
                message = f"Room updated. Removed {len(removed)} vacant bed{'s' if len(removed) != 1 else ''}."
                if reassigned:
                    message += " Occupied beds renumbered automatically."
                messages.success(request, message)
                return redirect('pg_rooms')

            form.add_error('total_shares', payload)
            room.refresh_from_db()
            share_stats = _room_share_breakdown(room)
    else:
        form = RoomForm(instance=room)
    return render(request, 'pgadmin/room_form.html', {"form": form, "room": room, "share_stats": share_stats})


@login_required
def application_pdf(request, app_id):
    """Generate a structured PDF of a resident application for PG Admins.
    Includes selfie (if available) and appends Aadhaar PDF pages when possible.
    """
    app = get_object_or_404(ResidentApplication, pk=app_id)
    # Authorization: must be PG Admin for this PG
    user = request.user
    pg_id = getattr(app, 'pg_id', None)
    if not _require_pg_admin(user) or not _admin_pgs(user).filter(id=pg_id).exists():
        messages.error(request, 'PG Admin access required for this PG.')
        return redirect('pg_resident_applications')

    # Lazy imports to avoid hard dependency at import-time
    try:
        from io import BytesIO
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
        from reportlab.lib import colors
        import requests, re
        from urllib.parse import urlparse, parse_qs
    except Exception as e:
        return HttpResponse(f"PDF generation dependencies missing: {e}", status=500)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    title = f"Resident Application — {app.name or app.user.first_name} {app.user.last_name}"
    story.append(Paragraph(title, styles['Title']))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"PG: {app.pg.name}", styles['Normal']))
    story.append(Paragraph(f"Room: {getattr(app.room, 'room_no', '—')} • Share: {getattr(getattr(app, 'booking', None), 'share_no', '—')}", styles['Normal']))
    story.append(Spacer(1, 12))

    # Helpers for downloading
    def _normalize_direct_url(url: str) -> str:
        if not url:
            return url
        try:
            pu = urlparse(url)
            host = pu.netloc.lower()
            # Google Drive: file/d/<id>/view or open?id=<id>
            if 'drive.google.com' in host:
                m = re.search(r"/file/d/([\w-]+)/", pu.path)
                file_id = None
                if m:
                    file_id = m.group(1)
                else:
                    qs = parse_qs(pu.query)
                    file_id = (qs.get('id') or qs.get('file_id') or [None])[0]
                if file_id:
                    return f"https://drive.google.com/uc?export=download&id={file_id}"
            # Dropbox: ensure dl=1
            if 'dropbox.com' in host and 'dl=0' in url:
                return url.replace('dl=0', 'dl=1')
        except Exception:
            return url
        return url

    def _download(url: str, accept: str) -> bytes | None:
        if not url:
            return None
        url2 = _normalize_direct_url(url)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': accept,
            'Referer': '',
        }
        try:
            r = requests.get(url2, headers=headers, timeout=20, allow_redirects=True)
            if r.ok and r.content:
                return r.content
        except Exception:
            return None
        return None

    def _download_with_type(url: str, accept: str):
        if not url:
            return None, None
        url2 = _normalize_direct_url(url)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': accept,
            'Referer': '',
        }
        try:
            r = requests.get(url2, headers=headers, timeout=20, allow_redirects=True)
            if r.ok and r.content:
                return r.content, r.headers.get('content-type', '').lower()
        except Exception:
            return None, None
        return None, None

    # Selfie
    if getattr(app, 'selfie_url', None):
        selfie_bytes = _download(app.selfie_url, 'image/*')
        if selfie_bytes:
            try:
                img = RLImage(BytesIO(selfie_bytes))
                img._restrictSize(180, 180)
                story.append(Paragraph("Selfie:", styles['Heading4']))
                story.append(img)
                story.append(Spacer(1, 8))
            except Exception:
                story.append(Paragraph("Selfie: [Unable to load image binary]", styles['Normal']))
        else:
            # Fallback: include link when download not possible
            story.append(Paragraph(f"Selfie: <link href='{app.selfie_url}'>Open online</link>", styles['Normal']))

    # Info table
    info = [
        ["Name", f"{app.name or app.user.first_name} {app.user.last_name}"],
        ["DOB", f"{app.dob or '—'}"],
        ["Age", f"{app.age or '—'}"],
        ["Phone", f"{app.phone or getattr(getattr(app.user, 'profile', None), 'phone', '—')}"],
        ["Email", f"{app.email or app.user.email}"],
        ["Food Pref", f"{app.food_pref or '—'}"],
        ["Marital", f"{app.marital_status or '—'}"],
        ["Date of Admission", f"{app.date_of_admission or '—'}"],
        ["Joining Date", f"{getattr(app.booking, 'joining_date', None) or getattr(app.booking, 'start_date', None) or '—'}"],
        ["Address", f"{app.address or '—'}"],
    ]
    table = Table(info, hAlign='LEFT', colWidths=[120, 360])
    table.setStyle(TableStyle([
        ('FONT', (0,0), (-1,-1), 'Helvetica', 9),
        ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.whitesmoke]),
        ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))

    # Family
    fam = [
        ["Father Name", f"{app.father_name or '—'}"],
        ["Father Phone or Emergency Contact 1", f"{app.father_phone or '—'}"],
        ["Mother Name", f"{app.mother_name or '—'}"],
        ["Mother Phone or Emergency Contact 2", f"{app.mother_phone or '—'}"],
    ]
    story.append(Paragraph("Family", styles['Heading4']))
    # Make column 1 wider to accommodate long labels (e.g., "Mother Phone or Emergency Contact 2")
    story.append(Table(
        fam,
        colWidths=[180, 300],
        style=[
            ('FONT', (0,0), (-1,-1), 'Helvetica', 9),
            ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])
        ]
    ))
    story.append(Spacer(1, 10))

    # Education / Work
    work = [
        ["Occupation", f"{app.occupation or '—'}"],
        ["Education", f"{app.education or '—'}"],
        ["Organisation", f"{app.org_name or '—'}"],
        ["Org Address", f"{app.org_address or '—'}"],
    ]
    story.append(Paragraph("Education / Work", styles['Heading4']))
    story.append(Table(work, colWidths=[120, 360], style=[('FONT', (0,0), (-1,-1), 'Helvetica', 9), ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey), ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])]))
    story.append(Spacer(1, 10))

    # Vehicle
    vehicle_rows = [["Has Vehicle", "Yes" if app.has_vehicle else "No"]]
    if app.has_vehicle:
        vehicle_rows.append(["Number", f"{app.vehicle_number or '—'}"])
        vehicle_rows.append(["Model", f"{app.vehicle_model or '—'}"])
    story.append(Paragraph("Vehicle", styles['Heading4']))
    story.append(Table(vehicle_rows, colWidths=[120, 360], style=[('FONT', (0,0), (-1,-1), 'Helvetica', 9), ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey), ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])]))
    story.append(Spacer(1, 10))

    # Aadhaar summary and prefetch document before building main PDF
    story.append(Paragraph("Documents", styles['Heading4']))
    story.append(Paragraph(f"Aadhaar/Other Card Number: {app.aadhaar_number or '—'}", styles['Normal']))

    aadhaar_pdf_bytes = None
    if getattr(app, 'aadhaar_file_url', None):
        content, ctype = _download_with_type(app.aadhaar_file_url, 'application/pdf, image/*')
        if content:
            # Heuristics to detect PDF vs Image
            is_pdf = content[:4] == b'%PDF' or (ctype and 'pdf' in ctype)
            if is_pdf:
                aadhaar_pdf_bytes = content
                story.append(Spacer(1, 6))
                story.append(Paragraph("Document: (attached PDF will be appended)", styles['Italic']))
            else:
                try:
                    story.append(Spacer(1, 6))
                    story.append(Paragraph("Document Image:", styles['Heading4']))
                    aimg = RLImage(BytesIO(content))
                    aimg._restrictSize(420, 560)  # fit nicely on A4
                    story.append(aimg)
                except Exception:
                    story.append(Paragraph(f"Document: <link href='{app.aadhaar_file_url}'>Open online</link>", styles['Normal']))
        else:
            story.append(Paragraph(f"Document: <link href='{app.aadhaar_file_url}'>Open online</link>", styles['Normal']))

    # Optional second image (back side)
    if getattr(app, 'aadhaar_file_url_2', None):
        try:
            content2, ctype2 = _download_with_type(app.aadhaar_file_url_2, 'image/*, application/octet-stream')
            if content2:
                story.append(Spacer(1, 6))
                story.append(Paragraph("Back Side Image:", styles['Heading4']))
                img2 = RLImage(BytesIO(content2))
                img2._restrictSize(420, 560)
                story.append(img2)
            else:
                story.append(Paragraph(f"Back Side: <link href='{app.aadhaar_file_url_2}'>Open online</link>", styles['Normal']))
        except Exception:
            story.append(Paragraph(f"Back Side: <link href='{app.aadhaar_file_url_2}'>Open online</link>", styles['Normal']))

    # Declarations
    decls = [
        ["Valuables", "Agreed" if app.decl_valuables else "Not agreed"],
        ["Notice", "Agreed" if app.decl_notice else "Not agreed"],
        ["Deposit", "Agreed" if app.decl_deposit else "Not agreed"],
        ["Truth", "Agreed" if app.decl_truth else "Not agreed"],
    ]
    story.append(Spacer(1, 6))
    story.append(Paragraph("Declarations", styles['Heading4']))
    story.append(Table(decls, colWidths=[120, 360], style=[('FONT', (0,0), (-1,-1), 'Helvetica', 9), ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey), ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.whitesmoke])]))

    # Build main doc
    doc.build(story)
    main_pdf = buf.getvalue()

    # Attempt to merge PDFs if possible
    final_bytes = main_pdf
    if aadhaar_pdf_bytes:
        try:
            from pypdf import PdfReader, PdfWriter
            main_reader = PdfReader(BytesIO(main_pdf))
            out = PdfWriter()
            for p in main_reader.pages:
                out.add_page(p)
            aadhaar_reader = PdfReader(BytesIO(aadhaar_pdf_bytes))
            for p in aadhaar_reader.pages:
                out.add_page(p)
            out_buf = BytesIO()
            out.write(out_buf)
            final_bytes = out_buf.getvalue()
        except Exception:
            # Fallback: keep main PDF only if merge fails
            final_bytes = main_pdf

    filename = f"Resident-Application-{(app.name or app.user.first_name or 'User').replace(' ', '-')}-{app.id}.pdf"
    resp = HttpResponse(final_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@login_required
def room_shares(request, pk):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    room = get_object_or_404(Room, pk=pk)
    if not _admin_pgs(request.user).filter(id=room.pg_id).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    shares = room.shares.order_by('share_no')
    forms = [ShareStatusForm(prefix=f"s{rs.id}", instance=rs) for rs in shares]
    if request.method == 'POST':
        any_saved = False
        for rs in shares:
            prev_status = rs.status
            form = ShareStatusForm(request.POST, prefix=f"s{rs.id}", instance=rs)
            if form.is_valid():
                new_status = form.cleaned_data.get('status')
                # If moving from vacant/vacant_from to reserved/occupied, collect user details and create or link booking
                if prev_status in [RoomShareStatus.VACANT, RoomShareStatus.VACANT_FROM] and new_status in [RoomShareStatus.RESERVED, RoomShareStatus.OCCUPIED]:
                    email = request.POST.get(f"s{rs.id}-new-email", "").strip()
                    first_name = request.POST.get(f"s{rs.id}-new-first_name", "").strip()
                    last_name = request.POST.get(f"s{rs.id}-new-last_name", "").strip()
                    phone = request.POST.get(f"s{rs.id}-new-phone", "").strip()
                    joining_raw = request.POST.get(f"s{rs.id}-new-joining", "").strip()
                    joining_date = parse_date(joining_raw) if joining_raw else None

                    if not email:
                        messages.error(request, f"Bed {rs.share_no}: Email is required to set {new_status}.")
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
                        messages.error(request, f"Bed {rs.share_no}: Could not create booking. User may already have an active booking in this PG.")
                        # Skip saving status change for this share
                        continue

                    # Save the bed status change after booking created and clear vacant_from if set
                    saved_rs = form.save()
                    if getattr(saved_rs, 'vacant_from', None):
                        saved_rs.vacant_from = None
                        saved_rs.save(update_fields=['vacant_from'])
                    # Feedback
                    if created_user:
                        messages.success(request, f"Bed {rs.share_no}: User created: {user.email}, booking: {booking.get_status_display()}.")
                    else:
                        messages.success(request, f"Bed {rs.share_no}: User linked: {user.email}, booking: {booking.get_status_display()}.")
                else:
                    # Normal save and inline occupant updates when already occupied
                    saved_rs = form.save()
                    # If status changed away from VACANT_FROM, clear the date
                    if prev_status == RoomShareStatus.VACANT_FROM and saved_rs.status != RoomShareStatus.VACANT_FROM and getattr(saved_rs, 'vacant_from', None):
                        saved_rs.vacant_from = None
                        saved_rs.save(update_fields=['vacant_from'])
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
        if rs.status in [RoomShareStatus.OCCUPIED, RoomShareStatus.VACANT_FROM]:
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
    # Enforce PG membership
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    booking.status = Booking.APPROVED
    booking.start_date = timezone.now().date()
    booking.save()
    share.status = RoomShareStatus.OCCUPIED
    share.save(update_fields=['status'])
    log(request.user, 'booking_approved', 'Booking', booking.id, f"Approved for room {booking.room.room_no} bed {booking.share_no}")
    # Notify user
    Notification.objects.create(user=booking.user, title="Booking approved", message=f"Your booking for {booking.room} bed {booking.share_no} was approved.")
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[booking.id]))
        send_mail(
            subject="PG-MS: Booking Approved",
            message=f"Your booking for {booking.room} bed {booking.share_no} was approved.\nPlease complete your resident application here: {link}",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.success(request, "Booking approved and user notified.")
    # AJAX response
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
        return JsonResponse({
            'ok': True,
            'action': 'booking_approve',
            'booking_id': booking.id,
            'share_id': share.id,
            'share_status': share.status,
            'message': 'Booking approved.'
        })
    return redirect('pg_bookings_pending')


@login_required
@transaction.atomic
def booking_reject(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.PENDING)
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    booking.status = Booking.REJECTED
    booking.save(update_fields=['status'])
    # On rejection, revert bed to appropriate availability state.
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
    log(request.user, 'booking_rejected', 'Booking', booking.id, f"Rejected for room {booking.room.room_no} bed {booking.share_no}")
    Notification.objects.create(user=booking.user, title="Booking rejected", message=f"Your booking for {booking.room} bed {booking.share_no} was rejected.")
    try:
        send_mail(
            subject="PG-MS: Booking Rejected",
            message=f"Your booking for {booking.room} bed {booking.share_no} was rejected.",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.info(request, "Booking rejected.")
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
        return JsonResponse({
            'ok': True,
            'action': 'booking_reject',
            'booking_id': booking.id,
            'share_id': share.id,
            'share_status': share.status,
            'vacant_from': share.vacant_from.isoformat() if getattr(share, 'vacant_from', None) else None,
            'message': 'Booking rejected.'
        })
    return redirect('pg_bookings_pending')


@login_required
def leaving_requests(request):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    pg = _active_pg(request)
    requests_qs = (
        Booking.objects
        .filter(room__pg=pg, leaving_date__isnull=False, status=Booking.APPROVED)
        .select_related('user', 'room', 'application')
        if pg else []
    )
    today = timezone.now().date() if pg else None
    return render(request, 'pgadmin/leaving_requests.html', {"pg": pg, "bookings": requests_qs, "pgs": list(_admin_pgs(request.user)), "today": today})


@login_required
def application_email_send(request, booking_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id)
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[booking.id]))
        sent = send_mail(
            subject="PG-MS: Complete Your Resident Application",
            message=f"Please complete your resident application here: {link}",
            from_email=None,
            recipient_list=[booking.user.email],
            fail_silently=False,
        )
        if sent:
            messages.success(request, f"Application link sent to {booking.user.email}.")
        else:
            messages.error(request, "Email was not sent. Please verify email settings.")
    except Exception as e:
        messages.error(request, f"Could not send email: {e}")
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
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    share = get_object_or_404(RoomShareStatus, room=booking.room, share_no=booking.share_no)
    today = timezone.now().date()
    updated_fields = []
    if not booking.leaving_confirmed_date:
        booking.leaving_confirmed_date = today
        updated_fields.append('leaving_confirmed_date')
    if booking.leaving_date and booking.leaving_date <= today:
        share.status = RoomShareStatus.VACANT
        share.vacant_from = None
    else:
        share.status = RoomShareStatus.VACANT_FROM
        share.vacant_from = booking.leaving_date
    share.save(update_fields=['status', 'vacant_from'])
    if updated_fields:
        booking.save(update_fields=updated_fields)
    try:
        if hasattr(booking.user, 'profile'):
            if getattr(booking.user.profile, 'is_pg_user', True):
                booking.user.profile.is_pg_user = False
                booking.user.profile.save(update_fields=['is_pg_user'])
    except Exception:
        pass
    log(request.user, 'leaving_confirmed', 'Booking', booking.id, f"Leaving confirmed; booking closed for room {booking.room.room_no} bed {booking.share_no}")
    if share.status == RoomShareStatus.VACANT:
        messages.success(request, f"Leaving confirmed and bed freed for room {booking.room.room_no}.")
    else:
        messages.success(request, f"Leaving confirmed for room {booking.room.room_no}. Bed will free on {booking.leaving_date}.")
    return redirect('pg_leaving_requests')


@login_required
@transaction.atomic
def leaving_delete(request, booking_id):
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('pg_leaving_requests')
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.APPROVED)
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    if not booking.leaving_confirmed_date:
        messages.error(request, "Confirm the leaving request before deleting resident data.")
        return redirect('pg_leaving_requests')
    summary = _cleanup_booking_after_leave(booking, actor=request.user, origin='manual_button')
    if summary.get('failed_files'):
        messages.warning(
            request,
            (
                f"Resident data deleted but {summary['failed_files']} file(s) could not be removed from Drive. "
                "Please review manually."
            ),
        )
    else:
        extra = ''
        if summary.get('deleted_files'):
            extra = f" {summary['deleted_files']} file(s) deleted from Drive."
        messages.success(request, f"Resident booking and application removed.{extra}")
    return redirect('pg_leaving_requests')


@login_required
@transaction.atomic
def leaving_reject(request, booking_id):
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('pg_leaving_requests')
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    booking = get_object_or_404(Booking, pk=booking_id, status=Booking.APPROVED)
    if not _admin_pgs(request.user).filter(id=(getattr(booking, 'pg_id', None) or getattr(getattr(booking, 'room', None), 'pg_id', None))).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    if booking.leaving_confirmed_date:
        messages.error(request, "Leaving request already confirmed. Use delete to remove resident data if needed.")
        return redirect('pg_leaving_requests')
    booking.leaving_date = None
    booking.save(update_fields=['leaving_date'])
    try:
        share = RoomShareStatus.objects.get(room=booking.room, share_no=booking.share_no)
        share.status = RoomShareStatus.OCCUPIED
        share.vacant_from = None
        share.save(update_fields=['status', 'vacant_from'])
    except RoomShareStatus.DoesNotExist:
        pass
    log(request.user, 'booking_leave_rejected', 'Booking', booking.id, f"Leave request rejected for room {booking.room.room_no} bed {booking.share_no}")
    messages.success(request, "Leave request rejected and cleared.")
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
        today = timezone.now().date()
        bookings = (
            Booking.objects
            .filter(
                room__pg=pg,
                status=Booking.APPROVED,
            )
            .filter(Q(leaving_date__isnull=True) | Q(leaving_date__gt=today))
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


@login_required
@transaction.atomic
def application_confirm(request, app_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    app = get_object_or_404(ResidentApplication, pk=app_id)
    if not _admin_pgs(request.user).filter(id=getattr(app, 'pg_id', None)).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    # Set status to confirmed
    app.status = ResidentApplication.CONFIRMED
    app.save(update_fields=['status'])
    # History
    from bookings.models import ApplicationStatusHistory
    ApplicationStatusHistory.objects.create(application=app, status=app.status, comment='Confirmed by PG admin', by_user=request.user)
    # Notify user
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('my_application'))
        send_mail(
            subject="PG-MS: Application Confirmed",
            message=f"Your resident application has been confirmed. You can view it here: {link}\nFurther edits are disabled unless requested by admin.",
            from_email=None,
            recipient_list=[app.user.email],
            fail_silently=True,
        )
    except Exception:
        pass
    messages.success(request, "Application confirmed.")
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
        return JsonResponse({'ok': True, 'action': 'application_confirm', 'application_id': app.id, 'status': app.status})
    return redirect('pg_resident_applications')


@login_required
@transaction.atomic
def application_reject(request, app_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    app = get_object_or_404(ResidentApplication, pk=app_id)
    if not _admin_pgs(request.user).filter(id=getattr(app, 'pg_id', None)).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    reason = (request.POST.get('reason') or '').strip()
    if request.method != 'POST' or not reason:
        messages.error(request, "Rejection reason is required.")
        return redirect('pg_resident_applications')
    app.status = ResidentApplication.REJECTED
    app.save(update_fields=['status'])
    from bookings.models import ApplicationStatusHistory
    ApplicationStatusHistory.objects.create(application=app, status=app.status, comment=reason, by_user=request.user)
    # Email user with reason and link to edit
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[app.booking_id]))
        send_mail(
            subject="PG-MS: Application Rejected",
            message=f"Your resident application was rejected for the following reason:\n\n{reason}\n\nPlease re-fill the form here: {link}",
            from_email=None,
            recipient_list=[app.user.email],
            fail_silently=False,
        )
    except Exception as e:
        messages.error(request, f"Failed to send rejection email: {e}")
    messages.info(request, "Application rejected and user notified.")
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
        return JsonResponse({'ok': True, 'action': 'application_reject', 'application_id': app.id, 'status': app.status})
    return redirect('pg_resident_applications')


@login_required
@transaction.atomic
def application_refill_request(request, app_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    app = get_object_or_404(ResidentApplication, pk=app_id)
    if not _admin_pgs(request.user).filter(id=getattr(app, 'pg_id', None)).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    comment = (request.POST.get('comment') or '').strip()
    app.status = ResidentApplication.REFILL_REQUESTED
    app.save(update_fields=['status'])
    from bookings.models import ApplicationStatusHistory
    ApplicationStatusHistory.objects.create(application=app, status=app.status, comment=comment, by_user=request.user)
    # Email user to edit (this is commonly triggered after confirmation to request changes)
    try:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('application_fill', args=[app.booking_id]))
        send_mail(
            subject="PG-MS: Application Update Requested",
            message=f"Updates are requested on your resident application. Please edit here: {link}\n\nDetails: {comment or 'Please update your details as discussed.'}",
            from_email=None,
            recipient_list=[app.user.email],
            fail_silently=False,
        )
    except Exception as e:
        messages.error(request, f"Failed to send update request email: {e}")
    messages.success(request, "Re-Fill request sent to user.")
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax'):
        return JsonResponse({'ok': True, 'action': 'application_refill', 'application_id': app.id, 'status': app.status})
    return redirect('pg_resident_applications')
