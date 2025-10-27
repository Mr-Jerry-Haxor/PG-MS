# Quick Reference: Using the New Booking System

## For PG Users

### How to Book

1. **Access Booking Page**
   - Navigate to: `/pg/<pg-slug>/quick-booking/`
   - Example: `/pg/sunrise-hostel/quick-booking/`

2. **Choose Booking Type** (Modal appears automatically)
   
   **Option A: Day wise booking** (Short-term stay)
   - No room selection needed
   - Admin assigns room later
   - Best for: Guests staying 1-7 days
   
   **Option B: Book now** (Traditional booking)
   - Choose room and bed immediately
   - Complete full application
   - Best for: Long-term residents
   
   **Option C: Book for future** (Future-dated)
   - See rooms becoming vacant soon
   - Book bed for future date
   - Best for: Advance planning (1+ weeks ahead)

3. **Fill Form** (fields vary by type)

4. **Submit** → Admin receives notification

---

## For PG Admins

### Accessing Day-Wise Bookings

**URL**: `/pgadmin/daywise-bookings/`

**From Dashboard**: Look for "Day-Wise Bookings" in navigation menu

### Managing Day-Wise Bookings

#### View All Bookings
- **Filter by status**: Pending, Approved, Rejected, Completed
- **Filter by date**: Start date from, End date to
- **Search**: Guest name or mobile number

#### Approve a Booking
1. Click "Approve" button
2. Select room from dropdown (only shows rooms with vacant beds)
3. Select bed number
4. **If payment received**:
   - Check "Payment Received"
   - Enter amount
   - Select payment type (Cash, UPI, Bank Transfer, Card)
5. Click "Approve & Assign Room"

**What happens**:
- Booking status → Approved
- Room & bed assigned
- Payment record created (if payment received)
- Bed status updated (OCCUPIED if start date is today/past, RESERVED if future)
- Audit log entry created

#### Reject a Booking
1. Click "Reject" button
2. Optionally enter rejection reason
3. Click "Confirm Rejection"

**What happens**:
- Booking status → Rejected
- Audit log entry created

#### View Details
- Click "View Details" to see:
  - All guest information
  - Selfie photo
  - Aadhaar documents
  - Stay dates and times
  - Purpose of stay

---

## For Developers

### Quick Code Reference

#### Check if DayWiseBooking exists for a period
```python
from bookings.models import DayWiseBooking
from datetime import date

bookings = DayWiseBooking.objects.filter(
    pg=pg,
    status=DayWiseBooking.APPROVED,
    start_date__lte=date.today(),
    end_date__gte=date.today()
)
```

#### Create a Payment with billing period
```python
from finance.models import Payment

payment = Payment.objects.create(
    pg=pg,
    amount=1500.00,
    from_date=date(2025, 1, 1),
    to_date=date(2025, 1, 15),
    payment_type='cash',
    remarks='Day-wise booking payment'
)
```

#### Get vacant rooms for day-wise booking
```python
from bookings.models import Room, RoomShareStatus

rooms_with_vacancy = []
for room in Room.objects.filter(pg=pg):
    vacant_shares = RoomShareStatus.objects.filter(
        room=room,
        status__in=[RoomShareStatus.VACANT, RoomShareStatus.VACANT_FROM]
    )
    if vacant_shares.exists():
        rooms_with_vacancy.append({
            'room': room,
            'vacant_shares': list(vacant_shares)
        })
```

#### Audit log a day-wise booking action
```python
from core.audit import log

log(
    actor=request.user,
    action='daywise_booking_approved',
    target_type='DayWiseBooking',
    target_id=booking.id,
    message=f'Approved booking for {booking.name}',
    meta={'room_id': room.id, 'share_no': share_no}
)
```

---

## Troubleshooting

### Camera Not Working (Selfie Capture)
**Problem**: getUserMedia() fails  
**Solution**: 
- Ensure site is accessed via HTTPS (camera API requires secure context)
- Check browser permissions for camera access
- Try different browser (Chrome/Firefox recommended)

### Room Not Appearing in Approval Form
**Problem**: No rooms shown in dropdown  
**Solution**:
- Ensure at least one bed has status VACANT or VACANT_FROM
- Check room belongs to the correct PG
- Verify room is not deleted/inactive

### Bed Status Not Updating After Approval
**Problem**: Bed still shows as VACANT after approval  
**Solution**:
- Check booking start_date vs today's date
- If start_date is future → status should be RESERVED
- If start_date is today/past → status should be OCCUPIED
- Check database transaction completed successfully

### Payment Not Created
**Problem**: Payment record missing after approval  
**Solution**:
- Ensure "Payment Received" checkbox was checked
- Verify payment amount was entered
- Check for transaction errors in logs
- Payment amount must be positive number

---

## Database Schema Quick Ref

### DayWiseBooking Table
```
id (PK)
pg_id (FK → PG)
room_id (FK → Room, nullable)
share_no (int, nullable)
name (varchar 200)
mobile (varchar 15)
emergency_contact (varchar 15)
selfie (ImageField)
aadhaar_doc1 (FileField)
aadhaar_doc2 (FileField, nullable)
start_date (date)
end_date (date)
start_time (time, nullable)
end_time (time, nullable)
purpose (text)
status (varchar 20: pending/approved/rejected/completed)
payment_received (boolean, default False)
payment_amount (decimal, nullable)
assigned_at (datetime, nullable)
assigned_by_id (FK → User, nullable)
created_at (auto)
updated_at (auto)
```

### Payment Table (Enhanced)
```
... existing fields ...
from_date (date, nullable)  # NEW
to_date (date, nullable)    # NEW
```

---

## URL Patterns

### User URLs
```
/pg/<slug>/quick-booking/                          → pg_quick_booking (GET: show form, POST: submit)
/pg/<slug>/quick-booking/api/rooms/                → pg_quick_rooms (API)
/pg/<slug>/quick-booking/api/rooms/<id>/shares/    → pg_quick_shares (API)
```

### Admin URLs
```
/pgadmin/daywise-bookings/                         → daywise_bookings_list
/pgadmin/daywise-bookings/<id>/                    → daywise_booking_detail
/pgadmin/daywise-bookings/<id>/approve/            → daywise_booking_approve
/pgadmin/daywise-bookings/<id>/reject/             → daywise_booking_reject
```

---

## Permissions

### User Requirements
- Must be logged in (`@login_required`)
- No active booking in the same PG (checked automatically)

### Admin Requirements
- Must be logged in (`@login_required`)
- Must be PG Admin (`_require_pg_admin()`)
- Can only manage bookings for their own PG

---

## Email Notifications

### When User Submits Day-Wise Booking
**Recipients**: All PG admins  
**Subject**: "PG-MS: Day-Wise Booking Request"  
**Content**: Guest name, mobile, stay period, purpose, link to admin panel

### When User Submits Future Booking
**Recipients**: All PG admins  
**Subject**: "PG-MS: Future Booking Request"  
**Content**: Guest name, phone, room/bed, joining date, link to applications

### When User Submits Book Now
**Recipients**: All PG admins  
**Subject**: "PG-MS: Resident Application Submitted"  
**Content**: Applicant details, room/bed, link to applications

---

## Best Practices

### For Admins
1. **Review day-wise bookings daily** - Check for new pending requests
2. **Approve/reject within 24 hours** - Keep guests informed
3. **Verify documents** - Check selfie and Aadhaar before approval
4. **Confirm payment before checking "Payment Received"** - Avoid discrepancies
5. **Use rejection reason** - Helps with record-keeping

### For Developers
1. **Always use transactions** - When updating multiple tables
2. **Validate dates** - end_date >= start_date, joining_date >= vacant_from
3. **Log important actions** - Use `core.audit.log()` for tracking
4. **Handle file uploads safely** - Validate file types and sizes
5. **Test on HTTPS** - Camera API requires secure context

---

## Common Queries

### Get all pending day-wise bookings for today
```python
from datetime import date
from bookings.models import DayWiseBooking

today = date.today()
pending_today = DayWiseBooking.objects.filter(
    pg=pg,
    status=DayWiseBooking.PENDING,
    start_date=today
).select_related('assigned_by')
```

### Get all occupied beds from day-wise bookings
```python
occupied_from_daywise = DayWiseBooking.objects.filter(
    pg=pg,
    status=DayWiseBooking.APPROVED,
    start_date__lte=today,
    end_date__gte=today
).values_list('room_id', 'share_no')
```

### Calculate total revenue from day-wise bookings
```python
from django.db.models import Sum

total = DayWiseBooking.objects.filter(
    pg=pg,
    payment_received=True
).aggregate(total=Sum('payment_amount'))['total'] or 0
```

---

**Last Updated**: January 2025  
**Version**: 1.0  
**Status**: Production Ready
