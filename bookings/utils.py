"""Utility functions for bookings app"""
from django.utils import timezone
from django.db.models import Q
from .models import Booking, RoomShareStatus, RoomSwap


def pending_booking_share_keys(pg=None, room=None):
    """Return a set of (room_id, share_no) pairs for pending bookings."""
    qs = Booking.objects.filter(status=Booking.PENDING)
    if pg is not None:
        qs = qs.filter(room__pg=pg)
    if room is not None:
        qs = qs.filter(room=room)
    return set(qs.values_list('room_id', 'share_no'))


def share_has_pending_booking(room, share_no):
    """Return True when the given room/share already has a pending booking."""
    return Booking.objects.filter(room=room, share_no=share_no, status=Booking.PENDING).exists()


def sync_room_share_statuses(pg=None):
    """
    Sync RoomShareStatus records based on actual Booking data AND pending future swaps.
    
    Logic:
    - If booking is APPROVED and joining_date is in the future: RESERVED
    - If booking is APPROVED and currently active (joined, not left): OCCUPIED
    - If booking is APPROVED and leaving_date is set but not confirmed: VACANT_FROM
    - If no active/pending booking: VACANT
    
    Future Swap Adjustments (applied after base sync):
    - Beds with incoming future swaps: RESERVED (someone scheduled to move here)
    - Beds with outgoing future swaps but no incoming: VACANT_FROM (person leaving, no replacement)
    
    Also handles:
    - Moving bookings with past leaving_date (and confirmed) to COMPLETED status
    
    Args:
        pg: PG instance to sync (None = sync all)
    
    Returns:
        dict with counts of updated statuses
    """
    today = timezone.now().date()
    stats = {
        'vacant': 0,
        'reserved': 0,
        'occupied': 0,
        'vacant_from': 0,
        'total_processed': 0,
        'bookings_completed': 0
    }
    
    # First: Mark past bookings as COMPLETED
    # These are bookings where leaving_date is in the past AND leaving_confirmed_date is set
    # OR leaving_date is in the past and status is APPROVED (they left without confirmation)
    _complete_past_bookings(pg, today, stats)
    
    # Get all shares for the PG (or all shares if pg=None)
    if pg:
        shares = RoomShareStatus.objects.filter(room__pg=pg).select_related('room')
    else:
        shares = RoomShareStatus.objects.all().select_related('room')
    
    # Second pass: sync based on bookings (existing logic)
    for share in shares:
        stats['total_processed'] += 1

        # A pending booking already claims the bed.  Treat it as reserved before
        # considering approved bookings so the admin status and all room pickers
        # agree about its availability.
        if Booking.objects.filter(
            room=share.room,
            share_no=share.share_no,
            status=Booking.PENDING,
        ).exists():
            if share.status != RoomShareStatus.RESERVED or share.vacant_from is not None:
                share.status = RoomShareStatus.RESERVED
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['reserved'] += 1
            continue
        
        # Find all APPROVED bookings for this share (only APPROVED, not COMPLETED)
        all_bookings = list(Booking.objects.filter(
            room=share.room,
            share_no=share.share_no,
            status=Booking.APPROVED
        ).order_by('joining_date', '-created_at'))
        
        if not all_bookings:
            # No approved booking - mark as VACANT
            if share.status != RoomShareStatus.VACANT:
                share.status = RoomShareStatus.VACANT
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['vacant'] += 1
            continue
        
        # Find the current/active booking (joined but not left)
        # Handle multiple tenants for same bed scenario:
        # - If Person A is leaving today and Person B swapped in today
        # - Person B (no leaving_date or future leaving_date) is the current occupant
        # - Person A (leaving_date == today) should be considered "leaving/gone"
        current_booking = None
        future_booking = None
        leaving_today_booking = None  # Track booking leaving today separately
        
        # First pass: categorize bookings
        active_candidates = []  # Bookings that could be the current occupant
        
        for booking in all_bookings:
            joining_date = booking.joining_date or booking.start_date
            leaving_date = booking.leaving_date
            
            # Skip bookings that have left (leaving_date in the past)
            if leaving_date and leaving_date < today:
                continue
            
            # Future booking (not yet joined)
            if joining_date and joining_date > today:
                if not future_booking:
                    future_booking = booking
                continue
            
            # Booking that has joined (joining_date <= today or no joining_date)
            if not joining_date or joining_date <= today:
                if leaving_date == today:
                    # This person is leaving today - track separately
                    leaving_today_booking = booking
                elif not leaving_date or leaving_date > today:
                    # This person is NOT leaving today - they're definitely here
                    active_candidates.append(booking)
        
        # Determine the current booking:
        # 1. Prefer bookings NOT leaving today (they're definitely still here)
        # 2. If no such booking, fall back to the one leaving today
        if active_candidates:
            # Multiple people not leaving today? Take the most recent one (latest joining_date)
            # This handles swap scenario: new person swapped in is the current occupant
            current_booking = max(active_candidates, key=lambda b: b.joining_date or b.start_date or today)
        elif leaving_today_booking:
            # Only the leaving person is here - they're still the current occupant until end of day
            current_booking = leaving_today_booking
        
        # Priority logic:
        # 1) Current booking with leaving_date set -> check if there's a future booking
        #    - If future booking exists -> RESERVED
        #    - If no future booking -> VACANT_FROM
        # 2) Current booking without leaving_date -> OCCUPIED
        # 3) Only future booking -> RESERVED
        # 4) No active booking -> VACANT
        
        if current_booking:
            joining_date = current_booking.joining_date or current_booking.start_date
            leaving_date = current_booking.leaving_date
            
            # Current booking has a leaving date (in the future or today)
            if leaving_date:
                # Check if there's a future booking after the leaving date
                if future_booking:
                    # Future booking exists -> mark as RESERVED
                    if share.status != RoomShareStatus.RESERVED:
                        share.status = RoomShareStatus.RESERVED
                        share.vacant_from = None
                        share.save(update_fields=['status', 'vacant_from'])
                        stats['reserved'] += 1
                else:
                    # No future booking -> mark as VACANT_FROM
                    if share.status != RoomShareStatus.VACANT_FROM or share.vacant_from != leaving_date:
                        share.status = RoomShareStatus.VACANT_FROM
                        share.vacant_from = leaving_date
                        share.save(update_fields=['status', 'vacant_from'])
                        stats['vacant_from'] += 1
            else:
                # Current booking without leaving date -> OCCUPIED
                if share.status != RoomShareStatus.OCCUPIED:
                    share.status = RoomShareStatus.OCCUPIED
                    share.vacant_from = None
                    share.save(update_fields=['status', 'vacant_from'])
                    stats['occupied'] += 1
        
        elif future_booking:
            # Only future booking (not yet joined) -> RESERVED
            if share.status != RoomShareStatus.RESERVED:
                share.status = RoomShareStatus.RESERVED
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['reserved'] += 1
        
        else:
            # No current or future booking - mark as VACANT
            if share.status != RoomShareStatus.VACANT:
                share.status = RoomShareStatus.VACANT
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['vacant'] += 1
    
    # Third pass: Apply future swap adjustments
    # This overrides the booking-based status for beds involved in pending future swaps
    _apply_future_swap_adjustments(pg, stats)
    
    return stats


def _complete_past_bookings(pg, today, stats):
    """
    Mark bookings as COMPLETED if:
    1. leaving_confirmed_date is set AND leaving_date is in the past (admin confirmed and date passed)
    2. OR leaving_date is today AND another APPROVED booking exists for the same bed
       (meaning someone has already taken over the bed)
    
    This ensures old bookings don't keep showing up in sync calculations,
    but UNCONFIRMED leaving requests still show up in the leaving requests page.
    
    Also archives completed tenants to OldTenant table for PG admin reference.
    """
    # Query: APPROVED bookings with confirmed leaving AND leaving_date in the past
    if pg:
        confirmed_past_bookings = Booking.objects.filter(
            pg=pg,
            status=Booking.APPROVED,
            leaving_confirmed_date__isnull=False,
            leaving_date__lt=today  # leaving_date is strictly in the past
        ).select_related('room', 'user', 'application')
    else:
        confirmed_past_bookings = Booking.objects.filter(
            status=Booking.APPROVED,
            leaving_confirmed_date__isnull=False,
            leaving_date__lt=today
        ).select_related('room', 'user', 'application')
    
    for booking in confirmed_past_bookings:
        # Archive to OldTenant before marking as completed
        _archive_to_old_tenant(booking)
        # Mark as completed
        booking.status = Booking.COMPLETED
        booking.save(update_fields=['status'])
        stats['bookings_completed'] += 1
    
    # Also handle: bookings leaving TODAY where someone else has already taken the bed
    # This happens when a swap executes or new booking starts on the same day as someone's leaving_date
    if pg:
        leaving_today_bookings = Booking.objects.filter(
            pg=pg,
            status=Booking.APPROVED,
            leaving_date=today
        ).select_related('room', 'user', 'application')
    else:
        leaving_today_bookings = Booking.objects.filter(
            status=Booking.APPROVED,
            leaving_date=today
        ).select_related('room', 'user', 'application')
    
    for booking in leaving_today_bookings:
        # Check if there's another APPROVED booking for the same bed
        # (someone who swapped in, was assigned, or is a new advance booking joining today)
        other_booking_exists = Booking.objects.filter(
            room=booking.room,
            share_no=booking.share_no,
            status=Booking.APPROVED
        ).exclude(pk=booking.pk).filter(
            # The other person has joined (joining_date <= today or null)
            Q(joining_date__lte=today) | Q(joining_date__isnull=True)
        ).filter(
            # The other person is NOT leaving today or in the past
            Q(leaving_date__isnull=True) | Q(leaving_date__gt=today)
        ).exists()
        
        if other_booking_exists:
            # Archive to OldTenant before marking as completed
            _archive_to_old_tenant(booking)
            # Someone else has taken this bed - mark the leaving person as COMPLETED
            booking.status = Booking.COMPLETED
            booking.save(update_fields=['status'])
            stats['bookings_completed'] += 1


def _archive_to_old_tenant(booking):
    """
    Archive booking data to OldTenant table.
    Only creates a record if the booking doesn't already exist in OldTenant.
    """
    try:
        from pgadmin.models import OldTenant
        
        # Check if this booking already exists in OldTenant (avoid duplicates)
        existing_archive = OldTenant.objects.filter(
            pg=booking.pg,
            original_booking_id=booking.id
        ).exists()
        
        if existing_archive:
            return  # Already archived
        
        app = getattr(booking, 'application', None)
        
        # Get name from application or user
        full_name = ''
        father_name = ''
        mother_name = ''
        email = ''
        phone = ''
        whatsapp_number = ''
        address = ''
        
        if app:
            full_name = app.name or ''
            father_name = app.father_name or ''
            mother_name = app.mother_name or ''
            email = app.email or ''
            phone = app.phone or ''
            whatsapp_number = app.whatsapp_number or ''
            address = app.address or ''
        
        # Fallback to user data if application data is missing
        if not full_name and booking.user:
            full_name = f"{booking.user.first_name or ''} {booking.user.last_name or ''}".strip() or booking.user.email.split('@')[0]
        if not email and booking.user:
            email = booking.user.email or ''
        
        # Only create OldTenant if we have at least a name
        if full_name:
            OldTenant.objects.create(
                pg=booking.pg,
                full_name=full_name,
                father_name=father_name,
                mother_name=mother_name,
                email=email,
                phone=phone,
                whatsapp_number=whatsapp_number,
                address=address,
                room_no=getattr(getattr(booking, 'room', None), 'room_no', ''),
                bed_no=str(booking.share_no) if booking.share_no else '',
                joining_date=booking.joining_date,
                leaving_date=booking.leaving_date,
                leaving_reason=booking.leaving_reason or '',
                advance_paid=booking.advance_paid or 0,
                advance_returned=booking.advance_returned_amount if booking.advance_returned else 0,
                original_user=booking.user,
                original_booking_id=booking.id,
                archived_by=None,  # System-triggered, no specific user
                dob=app.dob if app else None,
                age=app.age if app else None,
                father_phone=app.father_phone if app else '',
                mother_phone=app.mother_phone if app else '',
                emergency_contact=app.emergency_contact if app else '',
                food_pref=app.food_pref if app else '',
                marital_status=app.marital_status if app else '',
                education=app.education if app else '',
                occupation=app.occupation if app else '',
                org_name=app.org_name if app else '',
                org_address=app.org_address if app else '',
                has_vehicle=app.has_vehicle if app else False,
                vehicle_number=app.vehicle_number if app else '',
                vehicle_model=app.vehicle_model if app else '',
                aadhaar_number=app.aadhaar_number if app else '',
                selfie_url=app.selfie_url if app else getattr(booking.user, 'profile', None).selfie_url if hasattr(booking.user, 'profile') else '',
                aadhaar_file_url=app.aadhaar_file_url if app else getattr(booking.user, 'profile', None).aadhaar_file_url if hasattr(booking.user, 'profile') else '',
                aadhaar_file_url_2=app.aadhaar_file_url_2 if app else getattr(booking.user, 'profile', None).aadhaar_file_url_2 if hasattr(booking.user, 'profile') else '',
            )
    except Exception as e:
        # Don't fail the sync if archiving fails
        import logging
        logging.getLogger(__name__).warning(f"Failed to archive tenant data: {e}")


def _apply_future_swap_adjustments(pg, stats):
    """
    Adjust bed statuses based on pending future swaps.
    
    This handles scenarios like:
    - Room 101 Bed 1: Tenant leaving Dec 25, has incoming swap scheduled -> RESERVED
    - Room 102 Bed 2: Tenant has outgoing swap to 101/1, no incoming -> VACANT_FROM
    
    Chain swaps are also handled:
    - A leaves 201/1, B->201/1, C->B's old bed, D->C's old bed
    - 201/1: RESERVED (B coming)
    - B's old bed: RESERVED (C coming)  
    - C's old bed: RESERVED (D coming)
    - D's old bed: VACANT_FROM (D leaving, no one coming)
    """
    today = timezone.now().date()
    
    # Get all pending/approved future swaps for this PG
    if pg:
        pending_swaps = RoomSwap.objects.filter(
            status__in=[RoomSwap.PENDING, RoomSwap.APPROVED],
            is_future_swap=True,
            booking__room__pg=pg
        ).select_related('booking', 'from_room', 'to_room')
    else:
        pending_swaps = RoomSwap.objects.filter(
            status__in=[RoomSwap.PENDING, RoomSwap.APPROVED],
            is_future_swap=True
        ).select_related('booking', 'from_room', 'to_room')
    
    if not pending_swaps.exists():
        return
    
    # Build a map of bed movements
    # Key: (room_id, share_no)
    # Value: {'incoming_swap': swap or None, 'outgoing_swap': swap or None, 'effective_date': date}
    bed_movements = {}
    
    for swap in pending_swaps:
        from_key = (swap.from_room_id, swap.from_share_no)
        to_key = (swap.to_room_id, swap.to_share_no)
        
        if from_key not in bed_movements:
            bed_movements[from_key] = {'incoming_swap': None, 'outgoing_swap': None, 'effective_date': None}
        if to_key not in bed_movements:
            bed_movements[to_key] = {'incoming_swap': None, 'outgoing_swap': None, 'effective_date': None}
        
        # Mark source bed as having outgoing swap
        bed_movements[from_key]['outgoing_swap'] = swap
        bed_movements[from_key]['effective_date'] = swap.effective_date
        
        # Mark destination bed as having incoming swap
        bed_movements[to_key]['incoming_swap'] = swap
        if not bed_movements[to_key]['effective_date'] or swap.effective_date < bed_movements[to_key]['effective_date']:
            bed_movements[to_key]['effective_date'] = swap.effective_date
    
    # Apply status updates based on swap movements
    for (room_id, share_no), movements in bed_movements.items():
        has_incoming = movements['incoming_swap'] is not None
        has_outgoing = movements['outgoing_swap'] is not None
        effective_date = movements['effective_date']
        
        try:
            share = RoomShareStatus.objects.get(room_id=room_id, share_no=share_no)
            
            if has_incoming:
                # This bed has someone scheduled to move here via future swap
                # Mark as RESERVED regardless of current status
                incoming_swap = movements['incoming_swap']
                if incoming_swap.effective_date > today:
                    if share.status != RoomShareStatus.RESERVED:
                        share.status = RoomShareStatus.RESERVED
                        share.vacant_from = None
                        share.save(update_fields=['status', 'vacant_from'])
                        
            elif has_outgoing and not has_incoming:
                # This bed has someone leaving via future swap, no one coming
                # Mark as VACANT_FROM with the swap's effective date
                if effective_date and effective_date > today:
                    if share.status != RoomShareStatus.VACANT_FROM or share.vacant_from != effective_date:
                        share.status = RoomShareStatus.VACANT_FROM
                        share.vacant_from = effective_date
                        share.save(update_fields=['status', 'vacant_from'])
                        
        except RoomShareStatus.DoesNotExist:
            pass
