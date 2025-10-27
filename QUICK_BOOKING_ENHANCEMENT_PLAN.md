# Quick Booking Enhancement - Implementation Plan

## Overview
Major enhancement to quick booking system with 3 booking types and payment improvements.

## 1. Database Models

### DayWiseBooking Model ✅ CREATED
```python
class DayWiseBooking(TimeStampedModel):
    # Location (assigned by admin)
    pg, room, share_no
    
    # Guest details
    name, mobile, emergency_contact
    selfie (ImageField)
    document1, document2 (FileField - supports images or PDF)
    
    # Stay details  
    start_date, end_date, start_time, end_time
    purpose (TextField)
    
    # Status & Payment
    status (pending/approved/rejected/completed)
    payment_received (Boolean)
    payment_amount (Decimal)
    
    # Admin tracking
    assigned_at, assigned_by
```

### Migration Required
```bash
python manage.py makemigrations bookings
python manage.py migrate
```

## 2. Quick Booking Flow Changes

### Current Flow:
1. User opens quick booking link
2. Sees rooms/beds directly
3. Fills application form
4. Submits

### New Flow:
1. User opens quick booking link
2. **MODAL appears with 3 options:**
   - Day-wise booking
   - Book now
   - Book for future
3. Based on selection, different flow

## 3. Three Booking Types

### A. Day-wise Booking

**User Journey:**
1. Select "Day-wise booking" from modal
2. Form appears (NO room/bed selection):
   - Name *
   - Mobile number *
   - Emergency contact number *
   - Selfie (live capture) *
   - Documents (2 images OR 1 PDF)
   - Start date & time *
   - End date & time *
   - Purpose *
3. Submit → Creates DayWiseBooking record (status=PENDING)

**PG Admin Journey:**
1. New menu item: "Day-wise Bookings" 
2. List view showing all day-wise bookings with filters:
   - Status (Pending/Approved/Rejected/Completed)
   - Date range
3. Click to view details modal:
   - All submitted information
   - Selfie display
   - Document preview/download
4. Confirmation form:
   - Assign Room dropdown
   - Assign Bed dropdown
   - Payment received? (checkbox)
   - If checked: Amount field
   - Approve/Reject buttons
5. On Approve:
   - Update DayWiseBooking (status=APPROVED, room, bed, payment details)
   - If payment received:
     - Create Payment record
     - Send payment receipt email/PDF
   - Send confirmation notification to guest

### B. Book Now (Enhanced)

**User Journey:**
1. Select "Book now" from modal
2. See available rooms/beds
3. Fill current application form
4. Submit

**PG Admin Journey:**
1. Existing approval flow in Applications page
2. On approval modal, add:
   - Payment received? (checkbox)
   - If checked:
     - Amount field *
     - Payment type: Monthly Fee / Advance
   - If Advance selected:
     - Add to booking.advance_paid
     - Create Payment record (type='advance')
   - If Monthly Fee selected:
     - Create Payment record (type='fee')
   - Send payment receipt

### C. Book for Future

**User Journey:**
1. Select "Book for future" from modal
2. See available rooms/beds (with future availability)
3. Form appears:
   - Select Room *
   - Select Bed *
   - Booking date (auto-filled with today's date, readonly)
   - Joining date (date picker, must be >= today) *
   - Name *
   - Phone number *
4. Submit → Creates Booking record (status=PENDING, joining_date=selected)

**PG Admin Journey:**
1. Appears in Applications/Bookings list
2. On approval modal, add:
   - Payment received? (checkbox)
   - If checked:
     - Amount field *
     - Payment type: Monthly Fee / Advance *
   - If Advance selected:
     - Add to booking.advance_paid
     - Create Payment record (type='advance')
     - Mark share as RESERVED (if joining_date > today)
   - If Monthly Fee:
     - Create Payment record (type='fee')
   - Send payment receipt

## 4. Payment Enhancements

### Monthly Overview Page - Payment Creation

**Current:**
- Click "Create Payment" button
- Shows booking details
- Shows expected amount
- Create payment

**New Addition:**
Add fields to payment creation form:
- From Date (date picker) *
- To Date (date picker) *
- These represent the billing period

**Implementation:**
- Add `from_date` and `to_date` fields to Payment model
- Or store in payment notes/description
- Display in payment receipts: "Payment for period: DD/MM/YYYY to DD/MM/YYYY"

### Payment Receipt Automation

After ANY payment creation (advance or fee):
1. Generate PDF receipt with:
   - Payment ID
   - Date
   - Amount
   - Type (Advance/Monthly Fee)
   - Period (if from_date/to_date provided)
   - Tenant name
   - Room/Bed details
   - PG details
2. Send via email to tenant
3. Option to download/resend from payment list

## 5. UI/UX Components

### Initial Modal (quick_booking.html)
```html
<!-- Modal triggered on page load -->
<div class="modal" id="bookingTypeModal">
  <h3>Select Booking Type</h3>
  <div class="booking-type-cards">
    <div class="card" data-type="daywise">
      <i class="bi bi-calendar-day"></i>
      <h4>Day-wise Booking</h4>
      <p>Short-term stay (hours/days)</p>
    </div>
    <div class="card" data-type="booknow">
      <i class="bi bi-door-open"></i>
      <h4>Book Now</h4>
      <p>Move in immediately</p>
    </div>
    <div class="card" data-type="future">
      <i class="bi bi-calendar-check"></i>
      <h4>Book for Future</h4>
      <p>Reserve for later date</p>
    </div>
  </div>
</div>
```

### Selfie Capture Component
```html
<div class="selfie-capture">
  <video id="video" width="320" height="240" autoplay></video>
  <button id="capture">Capture Selfie</button>
  <canvas id="canvas" width="320" height="240" style="display:none;"></canvas>
  <img id="preview" />
</div>
<script>
// Access camera, capture image, convert to blob/file
</script>
```

### Document Upload
```html
<input type="file" name="document1" accept="image/*,.pdf">
<input type="file" name="document2" accept="image/*">
<small>Upload 2 images OR 1 PDF document</small>
```

## 6. Views & URLs

### New Views Required:

#### bookings/views.py
```python
def pg_quick_booking(request, pgslug):
    # Add booking_type parameter handling
    booking_type = request.GET.get('type') or request.POST.get('booking_type')
    
    if booking_type == 'daywise':
        return handle_daywise_booking(request, pgslug)
    elif booking_type == 'future':
        return handle_future_booking(request, pgslug)
    else:
        return handle_booknow(request, pgslug)  # existing logic

def handle_daywise_booking(request, pgslug):
    # Handle day-wise booking form submission
    # Create DayWiseBooking record
    pass

def handle_future_booking(request, pgslug):
    # Simplified booking form for future bookings
    # Create Booking with joining_date
    pass
```

#### pgadmin/views.py
```python
def daywise_bookings_list(request):
    # List all day-wise bookings for admin
    pass

def daywise_booking_detail(request, booking_id):
    # View/approve/reject day-wise booking
    pass

def daywise_booking_approve(request, booking_id):
    # Assign room/bed, handle payment, send receipt
    pass
```

### URL Patterns:

#### bookings/urls.py
```python
# Quick booking with type parameter
path('pg/<slug:pgslug>/quick-booking/', pg_quick_booking, name='pg_quick_booking'),
```

#### pgadmin/urls.py
```python
path('daywise-bookings/', daywise_bookings_list, name='pg_daywise_bookings'),
path('daywise-bookings/<int:booking_id>/', daywise_booking_detail, name='pg_daywise_booking_detail'),
path('daywise-bookings/<int:booking_id>/approve/', daywise_booking_approve, name='pg_daywise_booking_approve'),
```

## 7. Templates

### New Templates:
- `bookings/daywise_booking_form.html` - Day-wise booking form
- `bookings/future_booking_form.html` - Future booking form  
- `pgadmin/daywise_bookings.html` - List of day-wise bookings
- `pgadmin/daywise_booking_detail_modal.html` - Detail view/approval

### Modified Templates:
- `bookings/quick_booking.html` - Add initial modal
- `pgadmin/applications.html` - Add payment fields to approval
- `finance/monthly_overview.html` - Add from_date/to_date to payment form

## 8. Payment Receipt Generation

### Create utility function:
```python
# core/receipts.py or finance/receipts.py
def generate_payment_receipt_pdf(payment):
    # Use ReportLab or similar
    # Generate PDF with payment details
    return pdf_bytes

def send_payment_receipt_email(payment, user_email):
    # Attach PDF
    # Send email with receipt
    pass
```

### Call after payment creation:
```python
# After creating Payment object
receipt_pdf = generate_payment_receipt_pdf(payment)
send_payment_receipt_email(payment, booking.user.email)
```

## 9. Admin Dashboard Updates

### Add to sidebar/menu:
```html
<li>
  <a href="{% url 'pg_daywise_bookings' %}">
    <i class="bi bi-calendar-day"></i>
    Day-wise Bookings
    <span class="badge">{{ pending_daywise_count }}</span>
  </a>
</li>
```

### Update statistics:
- Add day-wise booking counts to dashboard
- Show pending day-wise bookings alert

## 10. Validation & Security

### Day-wise Booking:
- Validate start_date/end_date (end >= start)
- Validate mobile/emergency_contact format
- Validate file uploads (size, type)
- Sanitize purpose text

### Future Booking:
- Validate joining_date >= today
- Check room/bed availability on joining_date
- Prevent double booking

### Payment:
- Validate amount > 0
- Require payment type selection if payment_received=True
- Prevent duplicate payment creation

## 11. Migration Steps

1. Create DayWiseBooking model ✅
2. Run migrations
3. Add Payment.from_date and Payment.to_date (or use existing notes field)
4. Create views for day-wise bookings
5. Update quick_booking view to handle 3 types
6. Create templates
7. Add URL patterns
8. Implement selfie capture JS
9. Create payment receipt generation
10. Update admin approval flows
11. Test all 3 booking flows
12. Test payment receipt generation
13. Deploy

## 12. Testing Checklist

- [ ] Day-wise booking form submission
- [ ] Selfie capture works in browser
- [ ] Document upload (2 images)
- [ ] Document upload (1 PDF)
- [ ] PG admin sees day-wise bookings
- [ ] PG admin can assign room/bed
- [ ] Payment checkbox creates Payment record
- [ ] Receipt email sent after payment
- [ ] Future booking creates RESERVED share
- [ ] Future booking auto-activates on joining_date
- [ ] Book now payment at approval
- [ ] Monthly overview from_date/to_date
- [ ] Receipt shows payment period

## Implementation Priority

**Phase 1 (High Priority):**
1. DayWiseBooking model ✅
2. Run migrations
3. Day-wise booking user form
4. Day-wise booking admin view/approval
5. Payment at approval for all types

**Phase 2 (Medium Priority):**
6. Payment receipt generation
7. Receipt email automation
8. From_date/to_date in monthly overview
9. Future booking enhancements

**Phase 3 (Polish):**
10. Selfie capture with camera
11. Enhanced UI/UX
12. Mobile responsiveness
13. Notifications

**Status:** Phase 1 model created, pending migrations and view implementation.

Due to project dependency issues (allauth module), will create migration file manually if needed.

