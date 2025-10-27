# Quick Booking System - Testing Guide

## Test URL
```
http://127.0.0.1:8000/b/pg/<your-pg-slug>/
```

Example: `http://127.0.0.1:8000/b/pg/sri-laxmi-balaji/`

---

## ✅ What Should Happen

### 1. Initial Page Load
- ✅ Page loads without errors
- ✅ Bootstrap modal appears automatically
- ✅ Three booking type cards visible:
  1. **Day wise booking** (orange/yellow card)
  2. **Book now** (blue card)
  3. **Book for future** (green card)

### 2. Selecting Each Booking Type

#### Option A: Day wise booking
**Click**: Day wise booking card

**Expected**:
- Modal closes
- Day-wise booking form appears
- Fields visible:
  - Name (text input)
  - Mobile (text input)
  - Emergency Contact (text input)
  - Selfie (camera button + video preview)
  - Aadhaar Document 1 (file upload) - Required
  - Aadhaar Document 2 (file upload) - Optional
  - Start Date (date picker)
  - Start Time (time picker)
  - End Date (date picker)
  - End Time (time picker)
  - Purpose (textarea)
  - Submit button

**Test**: 
1. Click "Start Camera" for selfie
2. Allow camera permission (browser popup)
3. See live video preview
4. Click "Capture" button
5. See captured image preview
6. Optional: Click "Retake" to capture again
7. Upload Aadhaar doc (image or PDF)
8. Fill all required fields
9. Click Submit
10. ✅ Success message: "Day-wise booking request submitted successfully"
11. ✅ Redirected to dashboard
12. ✅ PG admin receives email notification

#### Option B: Book now
**Click**: Book now card

**Expected**:
- Modal closes
- Traditional booking form appears
- Room dropdown appears (AJAX loaded)
- After selecting room → Bed dropdown populates
- After selecting bed → Application form appears (Step 3)
- Complete application form with all fields
- Upload selfie and Aadhaar to Google Drive
- Submit creates Booking + ResidentApplication

**Test**:
1. Select a room from dropdown
2. Select a bed from dropdown
3. Fill application form (name, DOB, phone, etc.)
4. Upload selfie image
5. Upload Aadhaar documents
6. Select payment day (1-31)
7. Agree to declaration checkbox
8. Click Submit
9. ✅ Success message: "Booking request and application submitted"
10. ✅ Redirected to dashboard

#### Option C: Book for future
**Click**: Book for future card

**Expected**:
- Modal closes
- Future booking form appears
- Room dropdown shows rooms with vacant + vacant_from beds
- Bed dropdown shows:
  - VACANT beds (available now)
  - VACANT_FROM beds with "Available from [date]" label
- Joining date field with validation
- Name and phone fields

**Test**:
1. Select a room
2. Select a bed (may show "Available from [date]")
3. Enter joining date:
   - If bed is VACANT_FROM → must be >= vacant_from date
   - If bed is VACANT → can be any future date
4. Enter name and phone
5. Click Submit
6. ✅ Success message: "Future booking request submitted for [date]"
7. ✅ Bed reserved (status → RESERVED)
8. ✅ PG admin receives notification

---

## 🧪 Detailed Test Cases

### Test Case 1: Day-Wise Booking Submission
**Steps**:
1. Navigate to `/b/pg/sri-laxmi-balaji/`
2. Click "Day wise booking"
3. Fill form:
   - Name: "John Doe"
   - Mobile: "9876543210"
   - Emergency: "9123456789"
   - Capture selfie
   - Upload Aadhaar (1 file)
   - Start: Tomorrow, 10:00 AM
   - End: 3 days later, 6:00 PM
   - Purpose: "Business trip"
4. Submit

**Expected Result**:
- ✅ DayWiseBooking created with status=PENDING
- ✅ Selfie saved to media folder
- ✅ Aadhaar doc saved
- ✅ Email sent to all PG admins
- ✅ User sees success message

**Verify in Admin**:
- Go to `/pgadmin/daywise-bookings/`
- See new booking in list with status "Pending"
- Click "View Details" → see all info + documents
- Click "Approve" → assign room/bed → mark payment → submit
- ✅ Bed status updated to OCCUPIED/RESERVED
- ✅ Payment record created (if payment checked)

---

### Test Case 2: Future Booking with VACANT_FROM Bed
**Setup**: Create a room with a VACANT_FROM bed (vacant_from = 7 days from now)

**Steps**:
1. Navigate to `/b/pg/sri-laxmi-balaji/`
2. Click "Book for future"
3. Select the room
4. See bed showing "Available from [7 days from now]"
5. Select that bed
6. Try entering joining date = 3 days from now
   - ❌ Should show error: "Joining date must be on or after [vacant_from]"
7. Enter joining date = 7 days from now (or later)
   - ✅ Should accept
8. Enter name and phone
9. Submit

**Expected Result**:
- ✅ Booking created with joining_date = 7 days from now
- ✅ Bed status = RESERVED
- ✅ Admin notified

---

### Test Case 3: Camera Permission Denied
**Steps**:
1. Click "Day wise booking"
2. Click "Start Camera"
3. In browser popup → Click "Block" or "Deny"

**Expected Result**:
- ✅ Error message shown: "Camera access denied. Please allow camera permission and try again."
- ✅ Form still usable (can submit without selfie if you handle error gracefully)
- OR require selfie → show error on submit

**Note**: Camera API requires HTTPS in production!

---

### Test Case 4: Multiple Booking Types in One Session
**Steps**:
1. Start "Day wise booking" → fill form halfway → cancel
2. Start "Book now" → fill form → submit successfully
3. Try "Day wise booking" again

**Expected Result**:
- ✅ Modal appears again
- ✅ All three options still available (unless has_active prevents)
- ✅ No cross-contamination between booking type data

---

## 🐛 Common Issues & Solutions

### Issue 1: Modal doesn't appear
**Symptom**: Page loads but no modal visible

**Debug**:
1. Open browser console (F12)
2. Check for JavaScript errors
3. Verify Bootstrap JS is loaded
4. Check if `showBookingTypeModal()` is called on DOMContentLoaded

**Fix**: Ensure this code exists at end of template:
```javascript
document.addEventListener('DOMContentLoaded', function() {
  showBookingTypeModal();
});
```

---

### Issue 2: Room dropdown empty
**Symptom**: "Book now" or "Book for future" → room dropdown shows "No rooms available"

**Debug**:
1. Check browser console → Network tab
2. Look for API call to `/b/pg/<slug>/api/rooms/`
3. Check response JSON

**Possible Causes**:
- No rooms created in PG
- All beds are OCCUPIED/RESERVED
- For future booking: Need `include_vacant_from=true` parameter

**Fix**:
- Create rooms with vacant beds
- Or free up some beds by completing bookings

---

### Issue 3: Camera not working
**Symptom**: "Start Camera" doesn't show video

**Debug**:
1. Check browser console for errors
2. Look for: `getUserMedia` errors
3. Check URL scheme (must be HTTPS in production, localhost ok for dev)

**Fix**:
- Grant camera permission in browser
- Use HTTPS (not HTTP) in production
- Try different browser (Chrome/Firefox recommended)

---

### Issue 4: Selfie not submitting
**Symptom**: Form submits but selfie field empty in database

**Debug**:
1. Check if `daywise_selfie_data` hidden input has value
2. Should start with `data:image/png;base64,`
3. Check backend decoding in `handle_daywise_booking`

**Fix**: Ensure capture button sets the hidden input value:
```javascript
document.getElementById('selfieData').value = canvas.toDataURL('image/png');
```

---

### Issue 5: "NoReverseMatch" error
**Symptom**: Error: `'pg_quick_booking_rooms' is not a valid view function`

**Fix**: ✅ ALREADY FIXED! Template now uses correct URL names:
- `pg_quick_rooms` (not pg_quick_booking_rooms)
- `pg_quick_shares` (not pg_quick_booking_shares)

---

## 📊 Database Verification Queries

### Check Day-Wise Bookings
```python
from bookings.models import DayWiseBooking
DayWiseBooking.objects.all().values('name', 'mobile', 'status', 'start_date', 'end_date')
```

### Check Future Bookings
```python
from bookings.models import Booking
from datetime import date
today = date.today()
Booking.objects.filter(joining_date__gt=today, status='PENDING')
```

### Check Vacant Beds
```python
from bookings.models import RoomShareStatus
RoomShareStatus.objects.filter(
    status__in=['VACANT', 'VACANT_FROM']
).values('room__room_no', 'share_no', 'status', 'vacant_from')
```

---

## 🎯 Success Criteria

### User Experience
- ✅ Modal loads instantly
- ✅ Three options clearly labeled
- ✅ Form fields validate properly
- ✅ Helpful error messages
- ✅ Success confirmation after submit

### Backend
- ✅ Correct booking type created
- ✅ All data saved correctly
- ✅ File uploads successful
- ✅ Email notifications sent
- ✅ Audit logs created

### Admin Workflow
- ✅ Day-wise bookings appear in admin list
- ✅ Can approve/reject
- ✅ Room assignment works
- ✅ Payment tracking accurate
- ✅ Bed status updates correctly

---

## 🚀 Next Steps After Testing

1. **If all tests pass**:
   - Deploy to staging environment
   - Test on HTTPS (for camera API)
   - Test on mobile devices
   - Collect user feedback

2. **If issues found**:
   - Document issues in GitHub Issues
   - Prioritize critical bugs
   - Fix and re-test

3. **Optional Enhancements**:
   - Add payment receipt generation
   - Email guests directly (collect email in form)
   - SMS notifications via Twilio
   - WhatsApp integration

---

**Last Updated**: October 27, 2025  
**Status**: Ready for Testing  
**Critical Fix Applied**: URL names corrected (pg_quick_rooms, pg_quick_shares)
