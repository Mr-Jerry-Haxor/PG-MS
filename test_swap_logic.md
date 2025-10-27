# Room Swap Logic Test Cases

## Fixed Issues:

### 1. ✅ Import Organization
- **Issue**: RoomSwap was imported multiple times inside the function
- **Fix**: Added to top-level imports at line 22
- **Impact**: Cleaner code, better performance

### 2. ✅ Validation Logic Enhancement
- **Issue**: When checkbox not checked, occupied bed selection gave generic error
- **Fix**: Added specific check with clear error message
- **Error Message**: "Cannot swap to occupied bed. Please check 'Swap with occupied' checkbox if you want to exchange rooms with another tenant."

### 3. ✅ RoomSwap Reason Field
- **Issue**: Hardcoded reason "Exchanged with leaving tenant" for all exchange types
- **Fix**: Created reason_map dictionary with proper reasons:
  - "regular": "Room swap (regular move to vacant bed)"
  - "exchange": "Exchanged with leaving tenant"
  - "occupied": "Exchanged with occupied tenant"

### 4. ✅ Duplicate Event Listeners
- **Issue**: bedsSel change listener added multiple times when room changed
- **Fix**: Clone and replace element to remove old listeners before adding new one

### 5. ✅ Missing Data Attribute
- **Issue**: JavaScript tried to access `modal.dataset.currentRoomNo` which didn't exist
- **Fix**: Added `data-current-room-no="{{ sd.booking.room.room_no }}"` to modal

### 6. ✅ JavaScript Reference Error
- **Issue**: Used undefined variable `confirmCheckbox` in else block
- **Fix**: Re-query checkbox using `modal.querySelector()` in else block

### 7. ✅ Empty Room Description
- **Issue**: If parts array was empty, room text would be incomplete
- **Fix**: Added fallback: `parts.length > 0 ? parts.join(', ') : 'No available beds'`

### 8. ✅ CRITICAL: Self-Swap Prevention
- **Issue**: API didn't filter out current tenant's own bed
- **Fix**: Added `.exclude(room=booking.room, share_no=booking.share_no)` to both queries
- **Impact**: Prevents tenant from swapping to their own bed

## Test Scenarios:

### Scenario 1: Regular Vacant Swap
- Tenant A (Room 1, Bed 1) → Empty Bed (Room 2, Bed 3)
- ✅ Checkbox: Not checked
- ✅ Result: Bed 3 becomes OCCUPIED, Bed 1 becomes VACANT
- ✅ Logs: 1 RoomSwap record created
- ✅ Reason: "Room swap (regular move to vacant bed)"

### Scenario 2: Exchange with Leaving Tenant
- Tenant A (Room 1, Bed 1) → Leaving Tenant (Room 2, Bed 2)
- ✅ Checkbox: Not checked (VACANT_FROM allowed without checkbox)
- ✅ Result: Both tenants swap rooms
- ✅ Logs: 2 RoomSwap records created
- ✅ Reasons: Both "Exchanged with leaving tenant" or exchange partner name

### Scenario 3: Exchange with Occupied Tenant
- Tenant A (Room 1, Bed 1) → Tenant B (Room 2, Bed 3)
- ✅ Checkbox: MUST be checked
- ✅ Result: Both tenants swap rooms, both beds remain OCCUPIED
- ✅ Logs: 2 RoomSwap records created
- ✅ Reasons: "Exchanged with occupied tenant" / exchange partner name

### Scenario 4: Checkbox Not Checked for Occupied Bed
- Tenant A tries to select occupied bed without checkbox
- ✅ Checkbox: Not checked
- ✅ Result: ERROR - "Cannot swap to occupied bed. Please check 'Swap with occupied' checkbox..."
- ✅ Frontend: Occupied beds not shown in dropdown

### Scenario 5: Self-Swap Attempt (Same Room)
- Tenant A (Room 1, Bed 1) tries to select their own bed
- ✅ Result: Their own bed excluded from dropdown
- ✅ Backend: Additional check prevents same room/bed selection

### Scenario 6: Checkbox Toggle
- Admin checks "Swap with occupied" checkbox
- ✅ Result: Rooms dropdown refreshes with occupied counts
- ✅ Beds dropdown shows occupied beds with tenant names
- ✅ Unchecking: Dropdown refreshes, occupied beds hidden

## UI Validation:

### Room Dropdown Display:
- Without checkbox: "Room 101 — 2 vacant, 1 vacant from"
- With checkbox: "Room 101 — 2 vacant, 1 vacant from, 3 occupied"

### Bed Dropdown Display:
- Vacant: "Bed 1"
- Vacant From: "Bed 2 (Vacant from 2025-11-01) - John Doe"
- Occupied: "Bed 3 (Occupied) - Jane Smith"

### Alert Messages:
- **Occupied Swap**: 
  - Title: "Exchange Swap (Occupied Bed)"
  - Message: "Jane Smith will move to Room 101, Bed 1"
- **Leaving Tenant Swap**:
  - Title: "Exchange Swap (Leaving Tenant)"
  - Message: "John Doe (leaving tenant) will move to the current tenant's bed"

## Database Integrity:

### RoomSwap Records Created:
```python
# Regular Swap
RoomSwap(
    booking=booking,
    from_room=Room1, to_room=Room2,
    from_share_no=1, to_share_no=3,
    effective_date=today,
    status=COMPLETED,
    reason="Room swap (regular move to vacant bed)",
    processed_by=admin_user
)

# Exchange Swap (2 records)
# Record 1 (Tenant A)
RoomSwap(
    booking=booking_a,
    from_room=Room1, to_room=Room2,
    effective_date=today,
    status=COMPLETED,
    reason="Exchanged with occupied tenant",
    processed_by=admin_user
)

# Record 2 (Tenant B)
RoomSwap(
    booking=booking_b,
    from_room=Room2, to_room=Room1,
    effective_date=today,
    status=COMPLETED,
    reason="Exchanged with Tenant A during room swap",
    processed_by=admin_user
)
```

## Code Quality Improvements:

1. ✅ No duplicate imports
2. ✅ Clear error messages
3. ✅ Proper event listener cleanup
4. ✅ Self-swap prevention
5. ✅ Consistent reason tracking
6. ✅ Data attribute validation
7. ✅ Null-safe operations

All issues have been identified and fixed!
