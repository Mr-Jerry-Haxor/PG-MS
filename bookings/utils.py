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
        
        # Find all APPROVED bookings for this share
        all_bookings = Booking.objects.filter(
            room=share.room,
            share_no=share.share_no,
            status=Booking.APPROVED
        ).order_by('joining_date', '-created_at')
        
        if not all_bookings.exists():
            # No approved booking - mark as VACANT
            if share.status != RoomShareStatus.VACANT:
                share.status = RoomShareStatus.VACANT
                share.vacant_from = None
                share.save(update_fields=['status', 'vacant_from'])
                stats['vacant'] += 1
            continue
        
        # Find the current/active booking (joined but not left)
        current_booking = None
        future_booking = None
        
        for booking in all_bookings:
            joining_date = booking.joining_date or booking.start_date
            leaving_date = booking.leaving_date
            
            # Future booking (not yet joined)
            if joining_date and joining_date > today:
                if not future_booking:
                    future_booking = booking
            # Current/active booking (already joined, not left yet)
            elif (not joining_date or joining_date <= today):
                if not leaving_date or leaving_date >= today:
                    current_booking = booking
                    break  # Found active booking, stop searching
        
        # Priority logic:
        # 1) Current booking with leaving_date set -> check if there's a future booking
        #    - If future booking exists -> RESERVED
        #    - If no future booking -> VACANT_FROM
        # 2) Current booking without leaving_date -> OCCUPIED
        # 3) Only future booking -> RESERVED
        # 4) Past leaving (left already) -> check if future booking exists
        #    - If future booking exists -> RESERVED
        #    - If no future booking -> VACANT
        
        if current_booking:
            joining_date = current_booking.joining_date or current_booking.start_date
            leaving_date = current_booking.leaving_date
            
            # Current booking has a leaving date
            if leaving_date:
                # Check if leaving date is in the future
                if leaving_date > today:
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
                elif leaving_date == today:
                    # Leaving today - treat same as future leaving
                    if future_booking:
                        if share.status != RoomShareStatus.RESERVED:
                            share.status = RoomShareStatus.RESERVED
                            share.vacant_from = None
                            share.save(update_fields=['status', 'vacant_from'])
                            stats['reserved'] += 1
                    else:
                        if share.status != RoomShareStatus.VACANT_FROM or share.vacant_from != leaving_date:
                            share.status = RoomShareStatus.VACANT_FROM
                            share.vacant_from = leaving_date
                            share.save(update_fields=['status', 'vacant_from'])
                            stats['vacant_from'] += 1
                else:
                    # Past leaving date (already left)
                    # This shouldn't happen if we're cleaning up bookings properly,
                    # but handle it gracefully
                    if future_booking:
                        if share.status != RoomShareStatus.RESERVED:
                            share.status = RoomShareStatus.RESERVED
                            share.vacant_from = None
                            share.save(update_fields=['status', 'vacant_from'])
                            stats['reserved'] += 1
                    else:
                        if share.status != RoomShareStatus.VACANT:
                            share.status = RoomShareStatus.VACANT
                            share.vacant_from = None
                            share.save(update_fields=['status', 'vacant_from'])
                            stats['vacant'] += 1
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
    
    return stats
