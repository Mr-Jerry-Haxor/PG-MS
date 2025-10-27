"""Utility functions for bookings app"""
from django.utils import timezone
from django.db.models import Q
from .models import Booking, RoomShareStatus


def sync_room_share_statuses(pg=None):
    """
    Sync RoomShareStatus records based on actual Booking data.
    
    Logic:
    - If booking is APPROVED and joining_date is in the future: RESERVED
    - If booking is APPROVED and currently active (joined, not left): OCCUPIED
    - If booking is APPROVED and leaving_date is set but not confirmed: VACANT_FROM
    - If no active/pending booking: VACANT
    
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
        'total_processed': 0
    }
    
    # Get all shares for the PG (or all shares if pg=None)
    if pg:
        shares = RoomShareStatus.objects.filter(room__pg=pg).select_related('room')
    else:
        shares = RoomShareStatus.objects.all().select_related('room')
    
    for share in shares:
        stats['total_processed'] += 1
        
        # Find active/future bookings for this exact share
        # Priority: APPROVED bookings only (PENDING should not affect share status until approved)
        active_booking = Booking.objects.filter(
            room=share.room,
            share_no=share.share_no,
            status=Booking.APPROVED
        ).order_by('-created_at').first()
        
        if not active_booking:
            # No approved booking - mark as VACANT
            if share.status != RoomShareStatus.VACANT:
                share.status = RoomShareStatus.VACANT
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['vacant'] += 1
            continue
        
        # Determine status based on booking dates
        joining_date = active_booking.joining_date or active_booking.start_date
        leaving_date = active_booking.leaving_date

        # Priority logic (explicit and easy to reason about):
        # 1) Future joining -> RESERVED
        # 2) Future leaving -> VACANT_FROM (vacant_from = leaving_date)
        # 3) Past leaving (left already) -> VACANT
        # 4) Currently joined (joining_date <= today and (no leaving or leaving >= today)) -> OCCUPIED
        # 5) Fallback -> VACANT

        # Case 1: Future booking (joining_date is in the future)
        if joining_date and joining_date > today:
            if share.status != RoomShareStatus.RESERVED:
                share.status = RoomShareStatus.RESERVED
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['reserved'] += 1

        # Case 2: Has leaving_date in the future (regardless of confirmation)
        elif leaving_date and leaving_date > today:
            if share.status != RoomShareStatus.VACANT_FROM or share.vacant_from != leaving_date:
                share.status = RoomShareStatus.VACANT_FROM
                share.vacant_from = leaving_date
                share.save(update_fields=['status', 'vacant_from'])
                stats['vacant_from'] += 1

        # Case 3: Past leaving date -> mark VACANT
        elif leaving_date and leaving_date < today:
            if share.status != RoomShareStatus.VACANT:
                share.status = RoomShareStatus.VACANT
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['vacant'] += 1

        # Case 4: Currently occupied (joined and not left yet)
        elif (not joining_date or joining_date <= today) and (not leaving_date or leaving_date >= today):
            if share.status != RoomShareStatus.OCCUPIED:
                share.status = RoomShareStatus.OCCUPIED
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['occupied'] += 1

        # Fallback: no matching condition - mark as VACANT
        else:
            if share.status != RoomShareStatus.VACANT:
                share.status = RoomShareStatus.VACANT
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['vacant'] += 1
    
    return stats
