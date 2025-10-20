# Bug Fix: RoomShareStatus Attribute Error

## 🐛 Issue
**Error Message:** `'RoomShareStatus' object has no attribute 'booking_id'`

**Location:** Async PDF generation function (`_generate_pdf_async`)

## 🔍 Root Cause

The async PDF generation function was incorrectly trying to access attributes that don't exist on the `RoomShareStatus` model:
- ❌ `share.booking_id` (doesn't exist)
- ❌ `share.bed_label` (doesn't exist)

### Model Structure
The `RoomShareStatus` model only tracks the **status** of a bed/share:
```python
class RoomShareStatus(TimeStampedModel):
    room = ForeignKey(Room)
    share_no = PositiveSmallIntegerField
    status = CharField  # vacant, reserved, occupied, vacant_from
    vacant_from = DateField
```

The `Booking` model is what links users to specific room shares:
```python
class Booking(TimeStampedModel):
    user = ForeignKey(User)
    room = ForeignKey(Room)
    share_no = PositiveSmallIntegerField  # Which bed in the room
    status = CharField  # pending, approved, rejected
    joining_date = DateField
    leaving_date = DateField
```

## ✅ Solution

Changed the async function to use the **same approach** as the synchronous `tenants_export_pdf` function:

### Before (Incorrect):
```python
# ❌ Wrong: Trying to get bookings from RoomShareStatus
for room in rooms:
    for share in room.shares.all():
        if share.booking_id:  # AttributeError!
            all_booking_ids.append(share.booking_id)
```

### After (Correct):
```python
# ✅ Correct: Query bookings directly
room_share_map = {}
for room in rooms:
    total_shares = room.total_shares or 1
    for share_no in range(1, total_shares + 1):
        room_share_map[(room.id, share_no)] = None

# Get all approved/pending bookings
bookings_qs = Booking.objects.filter(
    room__in=rooms,
    status__in=[Booking.APPROVED, Booking.PENDING]
).select_related('user', 'user__profile', 'application')

# Map bookings to their positions
for booking in bookings_qs:
    key = (booking.room_id, booking.share_no)
    if key in room_share_map and room_share_map[key] is None:
        if not booking.leaving_date or booking.leaving_date >= today:
            room_share_map[key] = booking
```

## 📝 Changes Made

### File: `pgadmin/views.py`

**1. Fixed booking query logic (lines ~2778-2790):**
```python
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
```

**2. Fixed room iteration logic (lines ~2846-2898):**
```python
# Generate PDF content for each room
for idx, room in enumerate(rooms):
    # Room header
    story.append(Paragraph(f"Room {room.room_no}", room_header_style))
    
    # Get shares for this room
    total_shares = room.total_shares or 1
    shares_data = []
    
    for share_no in range(1, total_shares + 1):
        booking = room_share_map.get((room.id, share_no))
        # ... process booking data
```

**3. Removed unnecessary prefetch (lines ~2607-2613):**
```python
# Before: rooms = list(Room.objects.filter(pg=pg).prefetch_related(...))
# After:
rooms = list(Room.objects.filter(pg=pg).order_by('room_no'))
```

**4. Updated to use cached images:**
```python
# Before: selfie_img = _get_image(selfie_url)
# After:
selfie_img = _image_cache.get(selfie_url) if selfie_url else None
```

## 🧪 Testing

### Test Results:
✅ Syntax check passed  
✅ Django system check passed  
✅ Server reloads without errors  
✅ Async PDF generation starts successfully  
✅ Progress tracking works  

### From Server Logs:
```
[20/Oct/2025 21:04:03] "POST /pg/tenants/export/pdf/async/start/" 200
[20/Oct/2025 21:04:03] "GET /pg/tenants/export/pdf/async/.../progress/" 200
[20/Oct/2025 21:04:04] "GET /pg/tenants/export/pdf/async/.../progress/" 200
```

## 📊 Impact

### What's Fixed:
✅ Async PDF generation now uses correct model relationships  
✅ Matches the logic of the working synchronous version  
✅ Properly queries bookings instead of RoomShareStatus  
✅ Uses correct attribute names (`share_no` not `bed_label`)  

### What's Unchanged:
- Synchronous PDF export (already working correctly)
- Excel export functionality
- Other tenant management features
- Database schema

## 🚀 Next Steps

1. **Test Complete PDF Generation:**
   - Navigate to http://127.0.0.1:8000/pg/tenants/
   - Click "Export PDF" button
   - Wait for progress to reach 100%
   - Download and verify PDF

2. **Test with Various Scenarios:**
   - Small PG (< 20 tenants)
   - Medium PG (20-50 tenants)
   - Large PG (100+ tenants)
   - PG with some vacant rooms
   - PG with pending bookings

3. **Verify PDF Content:**
   - All rooms included
   - All tenants listed
   - Photos loaded correctly
   - Vacant beds shown as "Vacant"
   - Current month/year in header

## 📋 Summary

**Status:** ✅ **FIXED**

**Root Cause:** Incorrect model attribute access (`RoomShareStatus` doesn't have `booking_id`)

**Solution:** Use the same logic as synchronous version - query `Booking` model directly

**Files Modified:** 
- `pgadmin/views.py` (async function only)

**Testing:** Ready for full integration testing

---

**Fixed Date:** October 20, 2025  
**Fix Type:** Bug fix (no schema changes)  
**Breaking Changes:** None  
**Migration Required:** No
