from decimal import Decimal, InvalidOperation
from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, Min, OuterRef, Prefetch, Q, Sum
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

from bookings.models import Booking, ResidentApplication, Room, RoomShareStatus, ReferralCredit
from core.audit import log
from core.drive import drive_delete
from core.models import Notification
from finance.models import Fees
from django.urls import reverse
from django.http import HttpResponse
import calendar
from io import BytesIO

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter
except Exception:
    openpyxl = None
from .forms import PGForm, RoomForm, ShareStatusForm
from .models import PG, PGAdmin

@login_required
def booking_joining_update(request, booking_id):
    from bookings.models import Booking
    booking = get_object_or_404(Booking, pk=booking_id)
    # Authorization: must be a PG Admin and admin of the booking's PG
    u = request.user
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    redirect_url = request.META.get('HTTP_REFERER') or 'pg_resident_applications'

    if not _require_pg_admin(u) or not _admin_pgs(u).filter(id=getattr(booking, 'pg_id', None)).exists():
        message = 'PG Admin access required for this PG.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=403)
        messages.error(request, message)
        return redirect('pg_resident_applications')

    if request.method != 'POST':
        message = 'Unsupported request method.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=405)
        messages.error(request, message)
        return redirect('pg_resident_applications')
    date_str = (request.POST.get('joining_date') or '').strip()
    if not date_str:
        message = 'Joining date is required.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=400)
        messages.error(request, message)
        return redirect(redirect_url)
    dt = parse_date(date_str)
    if not dt:
        message = 'Invalid date format. Use YYYY-MM-DD.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=400)
        messages.error(request, message)
        return redirect(redirect_url)
    try:
        booking.joining_date = dt
        # If payment_date is not explicitly set or equals old joining_date, update it too
        old_payment_date = booking.payment_date
        old_joining_date = Booking.objects.filter(pk=booking_id).values_list('joining_date', flat=True).first()
        
        update_fields = ['joining_date']
        # Auto-update payment_date if it was previously synced with joining_date or not set
        if not old_payment_date or old_payment_date == old_joining_date:
            booking.payment_date = dt
            update_fields.append('payment_date')
        
        booking.save(update_fields=update_fields)
        success_message = f'Joining date updated to {dt}.'
        if is_ajax:
            return JsonResponse({
                'ok': True,
                'message': success_message,
                'value': dt.isoformat(),
                'value_iso': dt.isoformat(),
                'display': dt.isoformat(),
            })
        messages.success(request, success_message)
    except Exception as e:
        error_message = f'Could not update joining date: {e}'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': error_message}, status=500)
        messages.error(request, error_message)
    return redirect(redirect_url)


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
    ]

    return JsonResponse({
        'ok': True,
        'rooms': room_data,
        'current_room_id': booking.room_id,
        'current_share_no': booking.share_no,
    })



@login_required
def booking_payment_date_update(request, booking_id: int):
    """Allow PG admin to update the monthly payment_date for a booking from the PG admin UI.
    Mirrors finance.monthly_update_payment_date but scoped to PG admin area and redirects back.
    """
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    redirect_url = request.META.get('HTTP_REFERER') or 'pg_tenants'

    if request.method != 'POST':
        message = 'Invalid request method.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=405)
        messages.error(request, message)
        return redirect('pg_tenants')
    if not _require_pg_admin(request.user):
        message = 'PG Admin access required.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=403)
        messages.error(request, message)
        return redirect('pg_my')

    payment_raw = (request.POST.get('payment_date') or '').strip()
    payment_date = parse_date(payment_raw) if payment_raw else None
    if not payment_date:
        message = 'Select a valid payment date.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=400)
        messages.error(request, message)
        return redirect(redirect_url)

    booking = get_object_or_404(Booking, pk=booking_id)
    if not _admin_pgs(request.user).filter(id=getattr(booking, 'pg_id', None)).exists():
        message = 'You do not have access to the requested PG.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': message}, status=403)
        messages.error(request, message)
        return redirect(redirect_url)

    booking.payment_date = payment_date
    booking.save(update_fields=['payment_date'])
    log(request.user, 'booking_payment_date_updated', 'Booking', booking.id)
    success_message = 'Payment date updated.'
    if is_ajax:
        return JsonResponse({
            'ok': True,
            'message': success_message,
            'value': payment_date.isoformat(),
            'value_iso': payment_date.isoformat(),
            'display': payment_date.isoformat(),
        })
    messages.success(request, success_message)
    return redirect(redirect_url)

@login_required
def pg_referrals(request):
    """PG Admin view: list referral credits for PGs the user administers."""
    user = request.user
    if not _require_pg_admin(user):
        messages.error(request, 'PG Admin access required.')
        return redirect('pg_my')

    # Get PGs the user administers
    admin_pgs = list(_admin_pgs(user))
    if not admin_pgs:
        messages.error(request, 'You are not an admin for any PG.')
        return redirect('pg_my')

    pg_id = request.GET.get('pg')
    if pg_id:
        try:
            pg_id = int(pg_id)
        except (TypeError, ValueError):
            pg_id = None

    # If pg_id provided and user not admin for it, ignore
    if pg_id and not any(p.id == pg_id for p in admin_pgs):
        pg_id = None

    # Prefer explicit pg selection or default to first admin pg
    selected_pg = None
    if pg_id:
        selected_pg = next((p for p in admin_pgs if p.id == pg_id), None)
    else:
        selected_pg = admin_pgs[0]

    credits_qs = ReferralCredit.objects.filter(pg=selected_pg).select_related('referrer_user', 'referred_user', 'referrer_booking', 'referred_booking', 'application').order_by('-created_at')

    redeemed = credits_qs.filter(redeemed_on__isnull=False)
    pending = credits_qs.filter(redeemed_on__isnull=True)

    # Totals
    total_redeemed = redeemed.aggregate(total=Sum('redeemed_amount'))['total'] or 0
    total_pending = pending.aggregate(total=Sum('amount'))['total'] or 0

    context = {
        'pgs': admin_pgs,
        'pg': selected_pg,
        'redeemed': redeemed,
        'pending': pending,
        'total_redeemed': total_redeemed,
        'total_pending': total_pending,
    }
    return render(request, 'pgadmin/referrals.html', context)
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
def tenants_export_excel(request):
    """Export tenants in a formatted Excel workbook per PG and month layout described by the user.

    Layout rules implemented:
    - Leave two blank rows at top
    - One merged title row containing Month name, PG name/address/phone in bold center
    - Leave four blank rows after title
    - For each room in ascending room_no: create a block where first column is Room header and
      subsequent columns correspond to bed slots (room.total_shares). For each bed, write resident
      details in a single row (columns: room, name, joining, leaving, phone, email, father name, father phone, mother name, mother phone, ledger link)
    """
    if not _require_pg_admin(request.user):
        return HttpResponse('Forbidden', status=403)

    pg = _active_pg(request)
    if not pg:
        return HttpResponse('No active PG selected', status=400)

    if openpyxl is None:
        return HttpResponse('openpyxl not installed on server', status=500)

    # Determine month to show: current month
    today = timezone.now().date()
    month_name = calendar.month_name[today.month] + f' {today.year}'

    # Build rooms list ordered by room_no
    rooms = list(Room.objects.filter(pg=pg).order_by('room_no').prefetch_related('shares'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Tenants'

    row = 1
    # Leave two blank rows
    row += 2

    # Title: merge across some columns. We'll estimate max columns: for largest room, columns = 1 (room) + max_shares * 1 + ledger
    max_shares = max((r.total_shares or 1) for r in rooms) if rooms else 1
    cols_needed = 1 + max_shares * 1 + 10  # allocate extra for fields; final layout uses fixed columns per resident
    last_col = get_column_letter(max(6, cols_needed))

    title_cell = ws.cell(row=row, column=1)
    title_cell.value = f"{month_name} — {pg.name} • {pg.address or ''} • {pg.phone or ''}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal='center')
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(8, max_shares + 4))

    row += 1

    # Leave four blank rows
    row += 4

    # Column header template for each resident row
    headers = ['Room', 'Name', 'Joining', 'Leaving', 'Phone', 'Email', 'Father name', 'Father phone', 'Mother name', 'Mother phone', 'Ledger']

    # For each room, create rows. For each bed in room.total_shares, create one resident row.
    for room in rooms:
        # Room block header: write room number in bold
        ws.cell(row=row, column=1, value=f"Room {room.room_no}")
        ws.cell(row=row, column=1).font = Font(bold=True)
        row += 1

        # Write header row
        for ci, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=ci, value=h)
            c.font = Font(bold=True)
        row += 1

        # For each bed number, find occupant booking (approved) or pending occupant whose joining date <= today
        shares = list(room.shares.order_by('share_no'))
        # Ensure we iterate from 1..room.total_shares
        total = room.total_shares or len(shares) or 1
        for share_no in range(1, total + 1):
            # default empty row: include room and bed number
            values = [f"{room.room_no} - (BED {share_no})"] + [''] * (len(headers) - 1)

            # find booking
            booking = Booking.objects.filter(room=room, share_no=share_no, status__in=[Booking.APPROVED, Booking.PENDING]).select_related('user', 'user__profile').order_by('-created_at').first()
            # If booking exists and not yet left (leaving_date is None or in future), include
            include_person = False
            if booking:
                if not booking.leaving_date or booking.leaving_date >= today:
                    include_person = True

            if include_person and booking:
                user = booking.user
                app = getattr(booking, 'application', None)
                # Populate fields
                values[1] = f"{user.first_name} {user.last_name}".strip()
                joining = booking.joining_date or booking.start_date
                values[2] = joining.isoformat() if joining else ''
                values[3] = booking.leaving_date.isoformat() if booking.leaving_date else ''
                # phone/email preference: application.phone -> profile.phone -> user.email
                phone = getattr(app, 'phone', None) or getattr(getattr(user, 'profile', None), 'phone', '')
                values[4] = phone
                values[5] = getattr(app, 'email', None) or user.email or ''
                # parents info from application if present
                values[6] = getattr(app, 'father_name', '') if app else ''
                values[7] = getattr(app, 'father_phone', '') if app else ''
                values[8] = getattr(app, 'mother_name', '') if app else ''
                values[9] = getattr(app, 'mother_phone', '') if app else ''
                # Ledger link: reverse finance_ledger
                try:
                    ledger_url = request.build_absolute_uri(reverse('finance_ledger', kwargs={'user_id': user.id}))
                except Exception:
                    ledger_url = ''
                values[10] = ledger_url

            # write the row
            for ci, v in enumerate(values, start=1):
                cell = ws.cell(row=row, column=ci)
                if ci == 11 and v:
                    # Ledger hyperlink
                    cell.value = 'Ledger'
                    cell.hyperlink = v
                    cell.font = Font(color='0000EE', underline='single')
                    cell.alignment = Alignment(horizontal='left')
                else:
                    cell.value = v
            row += 1

        # leave two blank rows between rooms
        row += 2

    # Adjust column widths a bit
    for i in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 18

    # Prepare response
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_name = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in (pg.name or 'pg'))
    filename = f"{safe_name.replace(' ', '_')}_tenants_{today.isoformat()}.xlsx"
    resp = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@login_required
def tenants_export_pdf(request):
    """Export tenants as a structured portrait PDF with room-by-room cards.
    Optimized for large datasets with streaming and async processing.

    Layout:
    - Portrait mode (A4)
    - Header: PG name (bold, large), address (medium), phone (medium bold), current month/year (bold) on separate lines
    - For each room: room number header, then resident cards (one per bed)
    - Each card: selfie (left), checkbox (right), details (center): name, phone, joining, payment, leaving
    """
    if not _require_pg_admin(request.user):
        return HttpResponse('Forbidden', status=403)

    pg = _active_pg(request)
    if not pg:
        return HttpResponse('No active PG selected', status=400)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm, inch
        from reportlab.pdfgen import canvas
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, Image as RLImage, Flowable
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.lib import colors
        import io as _io
        import requests
        from datetime import datetime
        from concurrent.futures import ThreadPoolExecutor, as_completed
    except Exception as e:
        return HttpResponse(f"PDF generation dependencies missing: {e}", status=500)

    today = timezone.now().date()
    current_month_year = datetime.now().strftime('%B %Y')  # e.g., "October 2025"
    
    # PERFORMANCE FIX: Skip images for large PGs to avoid 502 timeout
    total_rooms = Room.objects.filter(pg=pg).count()
    SKIP_IMAGES = total_rooms > 50  # Skip images if more than 50 rooms
    
    # Fetch data with select_related for optimization
    rooms = list(Room.objects.filter(pg=pg).order_by('room_no').prefetch_related(
        Prefetch('shares', queryset=RoomShareStatus.objects.all())
    ))

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=10*mm, bottomMargin=10*mm, leftMargin=12*mm, rightMargin=12*mm)
    story = []
    styles = getSampleStyleSheet()

    # Custom styles - reduced font sizes and spacing
    pg_name_style = ParagraphStyle('PGName', parent=styles['Heading1'], fontSize=14, textColor=colors.HexColor("#000000"), alignment=TA_CENTER, spaceAfter=2)
    pg_address_style = ParagraphStyle('PGAddress', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#1a1a1a'), fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=1)
    pg_phone_style = ParagraphStyle('PGPhone', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#1a1a1a'), fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=1)
    pg_month_style = ParagraphStyle('PGMonth', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor('#000000'), fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=6)
    room_header_style = ParagraphStyle('RoomHeader', parent=styles['Heading2'], fontSize=11, textColor=colors.HexColor('#2c5aa0'), spaceAfter=3, spaceBefore=5)

    # PG Header
    story.append(Paragraph(f"<b>{pg.name}</b>", pg_name_style))
    story.append(Paragraph(pg.address or '', pg_address_style))
    story.append(Paragraph(f"<b>{pg.phone or ''}</b>", pg_phone_style))
    story.append(Paragraph(f"<b>{current_month_year}</b>", pg_month_style))
    story.append(Spacer(1, 4*mm))

    # Custom Flowable for outlined checkbox
    class OutlinedCheckbox(Flowable):
        def __init__(self, size=4*mm):
            Flowable.__init__(self)
            self.size = size
            self.width = size
            self.height = size
        
        def draw(self):
            self.canv.setStrokeColor(colors.black)
            self.canv.setFillColor(colors.white)
            self.canv.setLineWidth(0.5)
            self.canv.rect(0, 0, self.size, self.size, stroke=1, fill=1)

    # Helper to download image and create RLImage - with caching
    _image_cache = {}
    def _get_image(url, default_width=18*mm, default_height=22*mm):
        # PERFORMANCE FIX: Skip images for large PGs
        if SKIP_IMAGES:
            return None
        if not url:
            return None
        if url in _image_cache:
            return _image_cache[url]
        try:
            # Normalize drive/dropbox URLs
            if 'drive.google.com' in url and '/file/d/' in url:
                fid = url.split('/file/d/')[1].split('/')[0]
                url = f'https://drive.google.com/uc?export=download&id={fid}'
            elif 'dropbox.com' in url:
                url = url.replace('www.dropbox.com', 'dl.dropboxusercontent.com').replace('?dl=0', '')
            resp = requests.get(url, timeout=2, stream=True)  # Reduced from 5s to 2s
            resp.raise_for_status()
            img_data = _io.BytesIO(resp.content)
            img = RLImage(img_data, width=default_width, height=default_height)
            _image_cache[url] = img
            return img
        except Exception:
            return None

    # Pre-fetch all bookings in bulk for optimization
    all_booking_ids = []
    room_share_map = {}
    for room in rooms:
        total_shares = room.total_shares or 1
        for share_no in range(1, total_shares + 1):
            room_share_map[(room.id, share_no)] = None
    
    # Bulk query bookings
    bookings_qs = Booking.objects.filter(
        room__in=rooms,
        status__in=[Booking.APPROVED, Booking.PENDING]
    ).select_related('user', 'user__profile', 'application').order_by('-created_at')
    
    for booking in bookings_qs:
        key = (booking.room_id, booking.share_no)
        if key in room_share_map and room_share_map[key] is None:
            if not booking.leaving_date or booking.leaving_date >= today:
                room_share_map[key] = booking

    # Pre-download images in parallel for better performance (skip if large PG)
    image_urls = set()
    if not SKIP_IMAGES:
        for booking in room_share_map.values():
            if booking:
                user = booking.user
                app = getattr(booking, 'application', None)
                selfie_url = getattr(app, 'selfie_url', None) or getattr(getattr(user, 'profile', None), 'selfie_url', None)
                if selfie_url:
                    image_urls.add(selfie_url)
        
        # Download images in parallel (max 3 concurrent, reduced from 5)
        if image_urls:
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_to_url = {executor.submit(_get_image, url): url for url in image_urls}
                for future in as_completed(future_to_url, timeout=20):  # 20s max total
                    try:
                        future.result(timeout=2)  # 2s per image
                    except Exception:
                        pass  # Image download failed, will show placeholder

    # Build cards for each room
    for room in rooms:
        story.append(Paragraph(f"Room {room.room_no}", room_header_style))
        total_shares = room.total_shares or 1

        # Collect all cards for this room
        all_cards = []
        
        for share_no in range(1, total_shares + 1):
            booking = room_share_map.get((room.id, share_no))

            # Build single card with 3 columns: [selfie | details | checkbox]
            if booking:
                user = booking.user
                app = getattr(booking, 'application', None)
                selfie_url = getattr(app, 'selfie_url', None) or getattr(getattr(user, 'profile', None), 'selfie_url', None)
                selfie_img = _image_cache.get(selfie_url) if selfie_url else None

                name = f"{user.first_name} {user.last_name}".strip() or user.email
                phone = getattr(app, 'phone', None) or getattr(getattr(user, 'profile', None), 'phone', '') or ''
                joining = booking.joining_date or booking.start_date
                joining_str = joining.strftime('%d/%m/%y') if joining else '—'
                payment = booking.payment_date
                payment_str = payment.strftime('%d/%m/%y') if payment else '—'
                leaving = booking.leaving_date
                leaving_str = leaving.strftime('%d/%m/%y') if leaving else '—'

                # Column 1: Selfie
                if selfie_img:
                    selfie_cell = selfie_img
                else:
                    selfie_cell = Paragraph("<i>No Photo</i>", ParagraphStyle('TinyText', parent=styles['Normal'], fontSize=7, alignment=TA_CENTER))
                
                # Column 2: Details
                detail_style = ParagraphStyle('CardDetail', parent=styles['Normal'], fontSize=7, leading=9, wordWrap='CJK')
                details_lines = [
                    f"<b>{name}</b>",
                    f"Phone: {phone}",
                    f"Join: {joining_str}",
                    f"Pay: {payment_str}",
                    f"Leave: {leaving_str}"
                ]
                details_cell = Paragraph("<br/>".join(details_lines), detail_style)
                
                # Column 3: Checkbox
                checkbox_cell = OutlinedCheckbox(size=4*mm)
                
                # Build card
                single_card_data = [[selfie_cell, details_cell, checkbox_cell]]
                single_card = Table(single_card_data, colWidths=[18*mm, 35*mm, 7*mm], rowHeights=[22*mm])
                single_card.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
                    ('VALIGN', (1, 0), (1, 0), 'TOP'),
                    ('VALIGN', (2, 0), (2, 0), 'MIDDLE'),
                    ('ALIGN', (0, 0), (0, 0), 'CENTER'),
                    ('ALIGN', (2, 0), (2, 0), 'CENTER'),
                    ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('LEFTPADDING', (0, 0), (-1, -1), 2),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]))
                all_cards.append(single_card)
            else:
                # Empty bed card
                vacant_text = Paragraph("<i>VACANT</i>", ParagraphStyle('VacantText', parent=styles['Normal'], fontSize=8, alignment=TA_CENTER, textColor=colors.grey))
                empty_detail = Paragraph(f"<i>Bed {share_no}</i>", ParagraphStyle('EmptyDetail', parent=styles['Normal'], fontSize=7, textColor=colors.grey))
                checkbox_cell = OutlinedCheckbox(size=4*mm)
                
                empty_card_data = [[vacant_text, empty_detail, checkbox_cell]]
                empty_card = Table(empty_card_data, colWidths=[18*mm, 35*mm, 7*mm], rowHeights=[22*mm])
                empty_card.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
                    ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
                    ('VALIGN', (2, 0), (2, 0), 'MIDDLE'),
                    ('ALIGN', (0, 0), (0, 0), 'CENTER'),
                    ('ALIGN', (1, 0), (1, 0), 'LEFT'),
                    ('ALIGN', (2, 0), (2, 0), 'CENTER'),
                    ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('LEFTPADDING', (0, 0), (-1, -1), 2),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]))
                all_cards.append(empty_card)
        
        # Arrange cards in rows of 3
        cards_per_row = 3
        for i in range(0, len(all_cards), cards_per_row):
            row_cards = all_cards[i:i+cards_per_row]
            # Pad row if less than 3 cards
            while len(row_cards) < cards_per_row:
                row_cards.append(Paragraph("", styles['Normal']))  # empty placeholder
            
            # Create row table
            row_table = Table([row_cards], colWidths=[60*mm, 60*mm, 60*mm])
            row_table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            story.append(row_table)
            story.append(Spacer(1, 2*mm))

        # Add space between rooms
        story.append(Spacer(1, 3*mm))

    # Build PDF with error handling
    try:
        doc.build(story)
    except Exception as e:
        # Fallback: minimal PDF with error
        buf2 = _io.BytesIO()
        c = canvas.Canvas(buf2, pagesize=A4)
        c.drawString(50, 800, f"PDF generation error: {e}")
        c.showPage()
        c.save()
        pdf = buf2.getvalue()
        buf2.close()
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="tenants_error.pdf"'
        return resp

    pdf = buf.getvalue()
    buf.close()
    safe_name = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in (pg.name or 'pg'))
    filename = f"{safe_name.replace(' ', '_')}_tenants_{current_month_year.replace(' ', '_')}.pdf"
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


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
    # Payment date: prefer booking.payment_date, fallback to booking.joining_date or start_date
    booking = getattr(app, 'booking', None)
    payment_date = None
    if booking:
        payment_date = getattr(booking, 'payment_date', None) or getattr(booking, 'joining_date', None) or getattr(booking, 'start_date', None)
    story.append(Paragraph(f"Room: {getattr(app.room, 'room_no', '—')} • Share: {getattr(getattr(app, 'booking', None), 'share_no', '—')}", styles['Normal']))
    story.append(Paragraph(f"Payment date: {payment_date or '—'}", styles['Normal']))
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
        ["Date of Application", f"{app.date_of_admission or '—'}"],
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
            .prefetch_related('application', 'application__status_history')
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
    referral_options = []
    if pg:
        today = timezone.now().date()
        bookings = (
            Booking.objects
            .filter(
                room__pg=pg,
                status=Booking.APPROVED,
            )
            .filter(Q(leaving_date__isnull=True) | Q(leaving_date__gt=today))
            .select_related('user', 'room', 'application', 'application__referral_credit')
            .order_by('room__room_no', 'share_no')
        )
        referral_options = (
            Booking.objects
            .filter(
                room__pg=pg,
                status=Booking.APPROVED,
            )
            .filter(Q(leaving_date__isnull=True) | Q(leaving_date__gt=today))
            .select_related('user', 'room', 'user__profile')
            .order_by('user__first_name', 'user__last_name', 'room__room_no', 'share_no')
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
            "referral_options": referral_options,
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
    # Ensure referral credit record exists when applicable
    try:
        if app.referred_by_booking_id and not getattr(app, 'referral_credit', None):
            ref_booking = app.referred_by_booking
            if ref_booking and ref_booking.status == Booking.APPROVED:
                amount = Decimal(str(app.pg.referral_amount or 0))
                if amount > 0:
                    anchor = getattr(app.booking, 'payment_date', None) or getattr(app.booking, 'joining_date', None)
                    if anchor:
                        scheduled_month = anchor.replace(day=1)
                    else:
                        scheduled_month = timezone.now().date().replace(day=1)
                    ReferralCredit.objects.create(
                        pg=app.pg,
                        referrer_user=ref_booking.user,
                        referrer_booking=ref_booking,
                        referred_user=app.user,
                        referred_booking=app.booking,
                        application=app,
                        amount=amount,
                        scheduled_month=scheduled_month,
                        notes='Auto-created on confirmation',
                    )
    except Exception as exc:
        messages.warning(request, f"Referral credit could not be created automatically: {exc}")
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


@login_required
@transaction.atomic
def application_update_referral(request, app_id):
    if not _require_pg_admin(request.user):
        messages.error(request, "PG Admin access required.")
        return redirect('dashboard')
    app = get_object_or_404(
        ResidentApplication.objects.select_related('pg', 'booking', 'user', 'referral_credit'),
        pk=app_id,
    )
    if not _admin_pgs(request.user).filter(id=getattr(app, 'pg_id', None)).exists():
        messages.error(request, "PG Admin access required for this PG.")
        return redirect('dashboard')
    if request.method != 'POST':
        messages.error(request, "Invalid request method.")
        return redirect('pg_resident_applications')

    redirect_target = redirect('pg_resident_applications')
    action = (request.POST.get('action') or '').strip().lower()
    credit = getattr(app, 'referral_credit', None)

    # Clear referral
    if action == 'clear' or not (request.POST.get('referred_by_booking') or '').strip():
        if credit and credit.redeemed_on:
            messages.error(request, "Referral credit already redeemed; cannot clear it.")
            return redirect_target
        app.referred_by_booking = None
        app.save(update_fields=['referred_by_booking'])
        if credit:
            credit.delete()
        messages.success(request, "Referral cleared for this application.")
        return redirect_target

    # Validate selected booking
    try:
        ref_booking_id = int(request.POST.get('referred_by_booking'))
    except (TypeError, ValueError):
        messages.error(request, "Select a valid referrer.")
        return redirect_target

    ref_booking = (
        Booking.objects.select_related('user')
        .filter(pk=ref_booking_id, room__pg_id=app.pg_id, status=Booking.APPROVED)
        .first()
    )
    if not ref_booking:
        messages.error(request, "Selected referrer is not valid for this PG.")
        return redirect_target
    if ref_booking.id == app.booking_id:
        messages.error(request, "An application cannot reference its own booking as referrer.")
        return redirect_target
    if ref_booking.user_id == app.user_id:
        messages.error(request, "A resident cannot refer themselves.")
        return redirect_target

    amount_raw = (request.POST.get('amount') or '').strip()
    if amount_raw:
        try:
            amount = Decimal(amount_raw)
        except (InvalidOperation, ValueError):
            messages.error(request, "Enter a valid referral amount.")
            return redirect_target
    else:
        amount = Decimal(str(app.pg.referral_amount or 0))

    if amount <= 0:
        messages.error(request, "Set a referral amount greater than zero to record credits.")
        return redirect_target

    scheduled_input = (request.POST.get('scheduled_month') or '').strip()
    scheduled_month = None
    if scheduled_input:
        try:
            year_str, month_str = scheduled_input.split('-', 1)
            scheduled_month = date(int(year_str), int(month_str), 1)
        except Exception:
            messages.error(request, "Invalid referral month selection.")
            return redirect_target

    notes = (request.POST.get('notes') or '').strip()[:255]

    app.referred_by_booking = ref_booking
    app.save(update_fields=['referred_by_booking'])

    default_month = scheduled_month
    if not default_month:
        anchor = getattr(app.booking, 'payment_date', None) or getattr(app.booking, 'joining_date', None)
        if anchor:
            default_month = anchor.replace(day=1)
        else:
            today = timezone.now().date()
            default_month = today.replace(day=1)

    credit = getattr(app, 'referral_credit', None)
    if credit and credit.redeemed_on:
        # Allow updating notes or schedule if it aligns with redeemed month
        if credit.referrer_booking_id != ref_booking.id:
            messages.error(request, "Referral credit already redeemed; cannot change the referrer.")
            return redirect_target
        if amount != credit.amount:
            messages.error(request, "Referral credit already redeemed; cannot change the amount.")
            return redirect_target
        if scheduled_month and credit.redeemed_for_month and credit.redeemed_for_month != scheduled_month:
            messages.error(request, "Referral credit already redeemed for a different month.")
            return redirect_target
        update_fields = []
        if scheduled_month and credit.scheduled_month != scheduled_month:
            credit.scheduled_month = scheduled_month
            update_fields.append('scheduled_month')
        if notes != credit.notes:
            credit.notes = notes
            update_fields.append('notes')
        if update_fields:
            credit.save(update_fields=update_fields)
        messages.success(request, "Referral details updated.")
        return redirect_target

    if not credit:
        credit = ReferralCredit.objects.create(
            pg=app.pg,
            referrer_user=ref_booking.user,
            referrer_booking=ref_booking,
            referred_user=app.user,
            referred_booking=app.booking,
            application=app,
            amount=amount,
            scheduled_month=default_month,
            notes=notes,
        )
    else:
        credit.referrer_user = ref_booking.user
        credit.referrer_booking = ref_booking
        credit.referred_user = app.user
        credit.referred_booking = app.booking
        credit.amount = amount
        credit.notes = notes
        if scheduled_month or not credit.scheduled_month:
            credit.scheduled_month = scheduled_month or default_month
        credit.save(update_fields=[
            'referrer_user',
            'referrer_booking',
            'referred_user',
            'referred_booking',
            'amount',
            'notes',
            'scheduled_month',
        ])

    messages.success(
        request,
        f"Referral recorded: {ref_booking.user.get_full_name() or ref_booking.user.email} will receive ₹{amount:.2f}.",
    )
    return redirect_target


# ============================================================================
# ASYNC PDF GENERATION WITH PROGRESS TRACKING
# ============================================================================

@login_required
def tenants_export_pdf_async_start(request):
    """Start async PDF generation and return task ID"""
    from .pdf_tasks import PDFTaskManager
    import threading
    
    if not _require_pg_admin(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    pg = _active_pg(request)
    if not pg:
        return JsonResponse({'error': 'No active PG selected'}, status=400)
    
    # Check if user already has an active task for this PG
    task_id, existing_task = PDFTaskManager.get_user_active_task(request.user.id, pg.id)
    if existing_task:
        return JsonResponse({
            'task_id': task_id,
            'status': existing_task['status'],
            'message': 'Task already in progress'
        })
    
    # Create new task
    task_id = PDFTaskManager.create_task(request.user.id, pg.id, pg.name)
    
    # Start background thread for PDF generation
    thread = threading.Thread(
        target=_generate_pdf_async,
        args=(task_id, request.user.id, pg.id),
        daemon=True
    )
    thread.start()
    
    return JsonResponse({
        'task_id': task_id,
        'status': 'pending',
        'message': 'PDF generation started'
    })


@login_required
def tenants_export_pdf_async_progress(request, task_id):
    """Check PDF generation progress"""
    from .pdf_tasks import PDFTaskManager
    
    task = PDFTaskManager.get_task(task_id)
    
    if not task:
        return JsonResponse({'error': 'Task not found'}, status=404)
    
    # Verify user owns this task
    if task['user_id'] != request.user.id:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    
    return JsonResponse({
        'task_id': task_id,
        'status': task['status'],
        'progress': task['progress'],
        'message': task['message'],
        'error': task.get('error'),
    })


@login_required
def tenants_export_pdf_async_download(request, task_id):
    """Download completed PDF and delete task"""
    from .pdf_tasks import PDFTaskManager, get_pdf_storage_dir
    from datetime import datetime
    import os

    task = PDFTaskManager.get_task(task_id)
    fallback_mode = False

    if not task:
        # Recover from scenarios where the in-memory task dictionary is cleared (e.g., different worker)
        parts = task_id.split('_') if task_id else []
        fallback_user_id = None
        fallback_pg_id = None
        if len(parts) >= 4 and parts[0] == 'pdf':
            try:
                fallback_user_id = int(parts[1])
                fallback_pg_id = int(parts[2])
            except ValueError:
                fallback_user_id = None
        if fallback_user_id != request.user.id:
            return HttpResponse('Task not found', status=404)
        if fallback_pg_id is None or not _admin_pgs(request.user).filter(id=fallback_pg_id).exists():
            return HttpResponse('Task not found', status=404)

        fallback_file = os.path.join(get_pdf_storage_dir(), f"pdf_{task_id}.pdf")
        if not os.path.exists(fallback_file):
            return HttpResponse('Task not found', status=404)

        pg_name = PG.objects.filter(id=fallback_pg_id).values_list('name', flat=True).first() or 'PG'
        task = {
            'user_id': fallback_user_id,
            'pg_id': fallback_pg_id,
            'pg_name': pg_name,
            'status': 'completed',
            'file_path': fallback_file,
        }
        fallback_mode = True

    # Verify user owns this task
    if task['user_id'] != request.user.id:
        return HttpResponse('Forbidden', status=403)

    if task['status'] != 'completed':
        return HttpResponse('PDF not ready yet', status=400)

    file_path = task.get('file_path')
    if not file_path or not os.path.exists(file_path):
        return HttpResponse('PDF file not found', status=404)

    with open(file_path, 'rb') as f:
        pdf_data = f.read()

    if fallback_mode:
        try:
            os.remove(file_path)
        except OSError:
            pass
    else:
        PDFTaskManager.delete_task(task_id)

    response = HttpResponse(pdf_data, content_type='application/pdf')
    filename = f"{task['pg_name'].replace(' ', '_')}_tenants_{datetime.now().strftime('%B_%Y')}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    return response


@login_required
def tenants_export_pdf_async_cancel(request, task_id):
    """Cancel/delete a PDF generation task"""
    from .pdf_tasks import PDFTaskManager
    
    task = PDFTaskManager.get_task(task_id)
    
    if not task:
        return JsonResponse({'error': 'Task not found'}, status=404)
    
    # Verify user owns this task
    if task['user_id'] != request.user.id:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    
    # Delete task
    PDFTaskManager.delete_task(task_id)
    
    return JsonResponse({'message': 'Task cancelled'})


def _generate_pdf_async(task_id, user_id, pg_id):
    """
    Background function to generate PDF with progress tracking.
    This runs in a separate thread.
    """
    from .pdf_tasks import PDFTaskManager, get_pdf_storage_dir
    import os
    from datetime import datetime
    
    try:
        PDFTaskManager.update_task(
            task_id,
            status='processing',
            progress=5,
            message='Fetching PG data...'
        )
        
        # Get PG object
        pg = PG.objects.get(id=pg_id)
        
        PDFTaskManager.update_task(
            task_id,
            progress=10,
            message='Fetching rooms and tenants...'
        )
        
        # Import required libraries
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm, inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, Flowable
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.lib import colors
        import requests
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from io import BytesIO
        from PIL import Image as PILImage
        
        # Get current date and month/year
        today = timezone.now().date()
        current_month_year = datetime.now().strftime('%B %Y')
        
        PDFTaskManager.update_task(
            task_id,
            progress=15,
            message='Loading room data...'
        )
        
        # Fetch all rooms (we'll get bookings separately)
        rooms = list(Room.objects.filter(pg=pg).order_by('room_no'))
        
        total_rooms = len(rooms)
        
        PDFTaskManager.update_task(
            task_id,
            progress=20,
            message=f'Processing {total_rooms} rooms...'
        )
        
        # Create PDF file path
        pdf_dir = get_pdf_storage_dir()
        filename = f"pdf_{task_id}.pdf"
        file_path = os.path.join(pdf_dir, filename)
        
        # Create PDF document
        doc = SimpleDocTemplate(file_path, pagesize=A4, topMargin=10*mm, bottomMargin=10*mm, 
                               leftMargin=12*mm, rightMargin=12*mm)
        story = []
        
        # Styles
        styles = getSampleStyleSheet()
        pg_name_style = ParagraphStyle(
            'PGName',
            parent=styles['Heading1'],
            fontSize=18,
            textColor=colors.HexColor('#1a237e'),
            alignment=TA_CENTER,
            spaceAfter=3*mm,
            fontName='Helvetica-Bold'
        )
        pg_addr_style = ParagraphStyle(
            'PGAddr',
            parent=styles['Normal'],
            fontSize=10,
            alignment=TA_CENTER,
            spaceAfter=2*mm
        )
        pg_phone_style = ParagraphStyle(
            'PGPhone',
            parent=styles['Normal'],
            fontSize=10,
            alignment=TA_CENTER,
            spaceAfter=2*mm,
            fontName='Helvetica-Bold'
        )
        pg_month_style = ParagraphStyle(
            'PGMonth',
            parent=styles['Normal'],
            fontSize=10,
            alignment=TA_CENTER,
            spaceAfter=4*mm,
            fontName='Helvetica-Bold'
        )
        room_header_style = ParagraphStyle(
            'RoomHeader',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#0d47a1'),
            spaceAfter=2*mm,
            spaceBefore=3*mm,
            fontName='Helvetica-Bold'
        )
        label_style = ParagraphStyle(
            'Label',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.grey,
            leading=10
        )
        value_style = ParagraphStyle(
            'Value',
            parent=styles['Normal'],
            fontSize=9,
            leading=11,
            fontName='Helvetica-Bold'
        )
        
        # Header
        story.append(Paragraph(pg.name, pg_name_style))
        story.append(Paragraph(pg.address or '', pg_addr_style))
        if pg.phone:
            story.append(Paragraph(f"Phone: {pg.phone}", pg_phone_style))
        story.append(Paragraph(current_month_year, pg_month_style))
        story.append(Spacer(1, 2*mm))
        
        PDFTaskManager.update_task(
            task_id,
            progress=25,
            message='Downloading tenant images...'
        )
        
        # Image cache
        _image_cache = {}
        
        # Custom Flowable for checkbox outline
        class OutlinedCheckbox(Flowable):
            def __init__(self, size=5*mm, checked=False):
                Flowable.__init__(self)
                self.size = size
                self.checked = checked
                self.width = size
                self.height = size
            
            def draw(self):
                self.canv.setStrokeColor(colors.black)
                self.canv.setLineWidth(0.5)
                self.canv.rect(0, 0, self.size, self.size, stroke=1, fill=0)
                if self.checked:
                    self.canv.setStrokeColor(colors.black)
                    self.canv.setLineWidth(1)
                    self.canv.line(0.15*self.size, 0.5*self.size, 0.4*self.size, 0.25*self.size)
                    self.canv.line(0.4*self.size, 0.25*self.size, 0.85*self.size, 0.75*self.size)
        
        def _get_image(url, default_width=18*mm, default_height=22*mm):
            """Download and cache image with extended timeout and retry support"""
            if url in _image_cache:
                return _image_cache[url]
            
            if not url:
                return None
            
            # Normalize Drive/Dropbox URLs
            if 'drive.google.com' in url and '/file/d/' in url:
                fid = url.split('/file/d/')[1].split('/')[0]
                url = f"https://drive.google.com/uc?export=download&id={fid}"
            elif 'dropbox.com' in url:
                url = url.replace('?dl=0', '?dl=1')
            
            try:
                # Increased timeout to 10s for very reliable image loading
                resp = requests.get(url, timeout=10, stream=True)
                resp.raise_for_status()
                
                img_data = BytesIO(resp.content)
                pil_img = PILImage.open(img_data)
                
                # Resize if needed
                max_w, max_h = int(default_width * 2.83), int(default_height * 2.83)
                if pil_img.width > max_w or pil_img.height > max_h:
                    pil_img.thumbnail((max_w, max_h), PILImage.Resampling.LANCZOS)
                
                # Convert to RGB if needed
                if pil_img.mode in ('RGBA', 'LA', 'P'):
                    bg = PILImage.new('RGB', pil_img.size, (255, 255, 255))
                    if pil_img.mode == 'P':
                        pil_img = pil_img.convert('RGBA')
                    bg.paste(pil_img, mask=pil_img.split()[-1] if pil_img.mode in ('RGBA', 'LA') else None)
                    pil_img = bg
                
                # Save to BytesIO
                buf = BytesIO()
                pil_img.save(buf, format='JPEG', quality=85)
                buf.seek(0)
                
                from reportlab.platypus import Image as RLImage
                rl_img = RLImage(buf, width=default_width, height=default_height)
                _image_cache[url] = rl_img
                return rl_img
            
            except Exception as e:
                # Cache the failure to avoid retrying in the same session
                _image_cache[url] = None
                return None
        
        # Build room_share_map with all possible shares
        room_share_map = {}
        for room in rooms:
            total_shares = room.total_shares or 1
            for share_no in range(1, total_shares + 1):
                room_share_map[(room.id, share_no)] = None
        
        # Batch fetch bookings with related data
        bookings_qs = Booking.objects.filter(
            room__in=rooms,
            status__in=[Booking.APPROVED, Booking.PENDING]
        ).select_related('user', 'user__profile', 'application').order_by('-created_at')
        
        # Map bookings to their room/share positions
        for booking in bookings_qs:
            key = (booking.room_id, booking.share_no)
            if key in room_share_map and room_share_map[key] is None:
                if not booking.leaving_date or booking.leaving_date >= today:
                    room_share_map[key] = booking
        
        PDFTaskManager.update_task(
            task_id,
            progress=30,
            message='Loading images sequentially for best quality...'
        )
        
        # Pre-download images sequentially (one by one) for maximum reliability
        image_urls = set()
        for booking in room_share_map.values():
            if booking:
                user = booking.user
                app = getattr(booking, 'application', None)
                selfie_url = getattr(app, 'selfie_url', None) or getattr(getattr(user, 'profile', None), 'selfie_url', None)
                if selfie_url:
                    image_urls.add(selfie_url)
        
        # Download images one by one with retry logic for perfect loading
        if image_urls:
            completed = 0
            total_images = len(image_urls)
            
            for url in image_urls:
                # Try to load image with up to 3 retries
                max_retries = 3
                success = False
                
                for attempt in range(max_retries):
                    try:
                        result = _get_image(url)
                        if result is not None:
                            success = True
                            break
                        # If result is None, retry
                        import time
                        time.sleep(1)  # Wait 1 second before retry
                    except Exception as e:
                        if attempt < max_retries - 1:
                            import time
                            time.sleep(1)  # Wait before retry
                        continue
                
                completed += 1
                progress = 30 + int((completed / total_images) * 20)  # 30-50%
                status = "✓" if success else "✗"
                PDFTaskManager.update_task(
                    task_id,
                    progress=min(progress, 50),
                    message=f'Loading images: {completed}/{total_images} {status}'
                )
        
        PDFTaskManager.update_task(
            task_id,
            progress=55,
            message='Generating PDF pages...'
        )
        
        # Generate PDF content for each room
        for idx, room in enumerate(rooms):
            # Room header
            story.append(Paragraph(f"Room {room.room_no}", room_header_style))
            
            # Get shares for this room
            total_shares = room.total_shares or 1
            
            # Collect all cards for this room
            all_cards = []
            
            for share_no in range(1, total_shares + 1):
                booking = room_share_map.get((room.id, share_no))
                
                # Build single card with 3 columns: [selfie | details | checkbox]
                if booking:
                    user = booking.user
                    app = getattr(booking, 'application', None)
                    selfie_url = getattr(app, 'selfie_url', None) or getattr(getattr(user, 'profile', None), 'selfie_url', None)
                    
                    # Normalize URL before cache lookup (must match normalization during download)
                    normalized_url = None
                    if selfie_url:
                        normalized_url = selfie_url
                        # Google Drive: file/d/ID/view → uc?export=download&id=ID
                        if 'drive.google.com/file/d/' in normalized_url:
                            parts = normalized_url.split('/d/')
                            if len(parts) > 1:
                                file_id = parts[1].split('/')[0]
                                normalized_url = f'https://drive.google.com/uc?export=download&id={file_id}'
                        # Dropbox: ?dl=0 → ?dl=1
                        elif 'dropbox.com' in normalized_url and '?dl=0' in normalized_url:
                            normalized_url = normalized_url.replace('?dl=0', '?dl=1')
                    
                    selfie_img = _image_cache.get(normalized_url) if normalized_url else None
                    
                    name = f"{user.first_name} {user.last_name}".strip() or user.email
                    phone = getattr(app, 'phone', None) or getattr(getattr(user, 'profile', None), 'phone', '') or ''
                    joining = booking.joining_date or booking.start_date
                    joining_str = joining.strftime('%d/%m/%y') if joining else '—'
                    payment = booking.payment_date
                    payment_str = payment.strftime('%d/%m/%y') if payment else '—'
                    leaving = booking.leaving_date
                    leaving_str = leaving.strftime('%d/%m/%y') if leaving else '—'
                    
                    # Column 1: Selfie
                    if selfie_img:
                        selfie_cell = selfie_img
                    else:
                        selfie_cell = Paragraph("<i>No Photo</i>", ParagraphStyle('TinyText', parent=styles['Normal'], fontSize=7, alignment=TA_CENTER))
                    
                    # Column 2: Details
                    detail_style = ParagraphStyle('CardDetail', parent=styles['Normal'], fontSize=7, leading=9, wordWrap='CJK')
                    details_lines = [
                        f"<b>{name}</b>",
                        f"Phone: {phone}",
                        f"Join: {joining_str}",
                        f"Pay: {payment_str}",
                        f"Leave: {leaving_str}"
                    ]
                    details_cell = Paragraph("<br/>".join(details_lines), detail_style)
                    
                    # Column 3: Checkbox
                    checkbox_cell = OutlinedCheckbox(size=4*mm)
                    
                    # Build card
                    single_card_data = [[selfie_cell, details_cell, checkbox_cell]]
                    single_card = Table(single_card_data, colWidths=[18*mm, 35*mm, 7*mm], rowHeights=[22*mm])
                    single_card.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
                        ('VALIGN', (1, 0), (1, 0), 'TOP'),
                        ('VALIGN', (2, 0), (2, 0), 'MIDDLE'),
                        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
                        ('ALIGN', (2, 0), (2, 0), 'CENTER'),
                        ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
                        ('LEFTPADDING', (0, 0), (-1, -1), 2),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                        ('TOPPADDING', (0, 0), (-1, -1), 2),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                    ]))
                    all_cards.append(single_card)
                else:
                    # Empty bed card
                    vacant_text = Paragraph("<i>VACANT</i>", ParagraphStyle('VacantText', parent=styles['Normal'], fontSize=8, alignment=TA_CENTER, textColor=colors.grey))
                    empty_detail = Paragraph(f"<i>Bed {share_no}</i>", ParagraphStyle('EmptyDetail', parent=styles['Normal'], fontSize=7, textColor=colors.grey))
                    checkbox_cell = OutlinedCheckbox(size=4*mm)
                    
                    empty_card_data = [[vacant_text, empty_detail, checkbox_cell]]
                    empty_card = Table(empty_card_data, colWidths=[18*mm, 35*mm, 7*mm], rowHeights=[22*mm])
                    empty_card.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
                        ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
                        ('VALIGN', (2, 0), (2, 0), 'MIDDLE'),
                        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
                        ('ALIGN', (1, 0), (1, 0), 'LEFT'),
                        ('ALIGN', (2, 0), (2, 0), 'CENTER'),
                        ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
                        ('LEFTPADDING', (0, 0), (-1, -1), 2),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                        ('TOPPADDING', (0, 0), (-1, -1), 2),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                    ]))
                    all_cards.append(empty_card)
            
            # Arrange cards in rows of 3
            cards_per_row = 3
            for i in range(0, len(all_cards), cards_per_row):
                row_cards = all_cards[i:i+cards_per_row]
                # Pad row if less than 3 cards
                while len(row_cards) < cards_per_row:
                    row_cards.append(Paragraph("", styles['Normal']))  # empty placeholder
                
                # Create row table
                row_table = Table([row_cards], colWidths=[60*mm, 60*mm, 60*mm])
                row_table.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 2),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ]))
                story.append(row_table)
                story.append(Spacer(1, 2*mm))
            
            # Add space between rooms
            story.append(Spacer(1, 3*mm))
            
            # Update progress
            progress = 55 + int(((idx + 1) / total_rooms) * 35)  # 55-90%
            PDFTaskManager.update_task(
                task_id,
                progress=min(progress, 90),
                message=f'Generated {idx + 1}/{total_rooms} rooms...'
            )
        
        PDFTaskManager.update_task(
            task_id,
            progress=95,
            message='Building final PDF...'
        )
        
        # Build PDF
        doc.build(story)
        
        PDFTaskManager.update_task(
            task_id,
            status='completed',
            progress=100,
            message='PDF generated successfully!',
            file_path=file_path,
            completed_at=timezone.now()
        )
        
    except Exception as e:
        PDFTaskManager.update_task(
            task_id,
            status='failed',
            progress=0,
            message='PDF generation failed',
            error=str(e)
        )
