# Complaint System Updates - October 20, 2025

## Summary of Changes

This document outlines the improvements made to the complaint system based on user requirements.

---

## 1. Smart Booking Selection in Complaint Modal

### ✅ Auto-Select Single Booking
- **Before**: Users always had to select their booking from a dropdown
- **After**: If user has only ONE active booking, it's automatically selected
- **UI Change**: Shows an info alert with the booking details instead of a dropdown
- **Message**: "Booking: [PG Name] - Room [X] (Share [Y])"

### ✅ Multiple Bookings Support
- **Behavior**: If user has multiple active bookings, dropdown is shown
- **Options**: All active bookings listed for selection

---

## 2. Active Booking Detection (Leaving Date Logic)

### ✅ Smart Button Visibility
The "Raise Complaint" button now only shows when:
1. User has at least ONE active booking
2. Booking status is "approved"
3. **NEW**: No leaving date OR leaving date is today or in the future

### ✅ Filter Logic
```python
# Filters applied:
- status = 'approved'
- leaving_date IS NULL (never set a leaving date)
  OR
- leaving_date >= today (still staying, not yet left)
```

### ✅ What This Means
- ✅ Button shows: User with approved booking, no leaving date
- ✅ Button shows: User with approved booking, leaving date is tomorrow
- ✅ Button shows: User with approved booking, leaving date is today
- ❌ Button hidden: User with approved booking, leaving date was yesterday
- ❌ Button hidden: User who already left the PG

---

## 3. Internal Comments Privacy

### ✅ Public vs Internal Comments
- **Internal Comments**: Admin-only notes, NOT visible to users
- **Public Comments**: Visible to both admins and users

### ✅ User Views Updated
All user-facing views now filter out internal comments:

#### Dashboard Accordion
- Shows only public comments
- Comment count shows only public comments
- Template: `templates/dashboard.html`

#### My Complaints List
- Comment count shows only public comments  
- Template: `templates/accounts/complaints/my_complaints.html`

#### Complaint Detail Page
- Shows only public comments
- Template: `templates/accounts/complaints/complaint_detail.html`

### ✅ Technical Implementation
```python
# In views:
complaint.public_comment_count = complaint.comments.filter(is_internal=False).count()

# In complaint_detail view:
comments = complaint.comments.filter(is_internal=False).select_related('user')
```

---

## 4. Code Changes Summary

### Files Modified

#### 1. `accounts/complaint_views.py`
- Updated `create_complaint()`: 
  - Added leaving date filter
  - Auto-select booking if only one active
- Updated `my_complaints()`: 
  - Added public comment count calculation
- Updated `complaint_detail()`: 
  - Already filtered internal comments ✅

#### 2. `core/views.py`
- Updated `dashboard()`:
  - Added `has_active_booking` flag
  - Added `active_bookings_count` 
  - Added `active_bookings` list with leaving date filter
  - Added public comment count to complaints

#### 3. `templates/dashboard.html`
- Updated button visibility: `{% if has_active_booking %}`
- Updated modal booking selection:
  - Single booking: Shows info alert + hidden input
  - Multiple bookings: Shows dropdown
- Updated comment count display: `{{ complaint.public_comment_count }}`

#### 4. `templates/accounts/complaints/my_complaints.html`
- Updated comment count: `{{ complaint.public_comment_count }}`

---

## 5. User Experience Improvements

### For PG Users:
1. **Simpler complaint creation**: No dropdown if you have only one booking
2. **Accurate button visibility**: Button hidden after leaving the PG
3. **Privacy respected**: Can't see internal admin notes
4. **Accurate counts**: Comment counts show only public comments

### For PG Admins:
1. **Internal notes feature works**: Can add private notes users won't see
2. **Comment privacy control**: Toggle `is_internal` when adding comments
3. **Full transparency**: Can see all comments (public + internal)

---

## 6. Testing Checklist

### Test Single Booking Auto-Select
- [ ] User with 1 active booking sees info alert (no dropdown)
- [ ] Complaint is created successfully
- [ ] Redirects back to dashboard after submit

### Test Multiple Bookings
- [ ] User with 2+ active bookings sees dropdown
- [ ] Can select different bookings
- [ ] Form validation works

### Test Leaving Date Logic
- [ ] Button shows when leaving_date is NULL
- [ ] Button shows when leaving_date is today
- [ ] Button shows when leaving_date is in future
- [ ] Button HIDDEN when leaving_date was yesterday
- [ ] Button HIDDEN when user has no approved bookings

### Test Comment Privacy
- [ ] User sees only public comments on dashboard
- [ ] User sees only public comments in complaint list
- [ ] User sees only public comments in complaint detail
- [ ] Admin sees all comments (public + internal)
- [ ] Comment counts are accurate (exclude internal)

---

## 7. Database Queries Optimization

### Efficient Queries Used:
```python
# Active bookings with leaving date filter
active_bookings = Booking.objects.filter(
    user=request.user,
    status=Booking.APPROVED
).filter(
    Q(leaving_date__isnull=True) | Q(leaving_date__gte=today)
).select_related('pg', 'room')

# Complaints with public comment counts
user_complaints = Complaint.objects.filter(
    user=request.user,
    booking__in=active_bookings
).select_related('pg', 'booking').prefetch_related('comments')
```

### Performance Benefits:
- ✅ `select_related()`: Reduces database queries for related objects
- ✅ `prefetch_related()`: Efficiently loads comments
- ✅ Single query for comment counts per complaint
- ✅ Filter at database level, not in Python

---

## 8. Security Considerations

### Data Privacy:
- ✅ Users can only view their own complaints
- ✅ Internal comments never exposed to users
- ✅ Booking validation ensures users can't create complaints for others' bookings
- ✅ Active booking check prevents complaints after leaving

### Access Control:
- ✅ Login required for all complaint views
- ✅ Complaint ownership verified in detail view
- ✅ Auto-redirect if no active bookings

---

## Summary

All requested features have been successfully implemented:

1. ✅ Smart booking selection (auto-select if single booking)
2. ✅ "Raise Complaint" button shows only for active bookings (respecting leaving dates)
3. ✅ Internal comments hidden from users
4. ✅ Public comment counts accurate across all views
5. ✅ Improved UX with contextual UI elements
6. ✅ Optimized database queries
7. ✅ Enhanced privacy and security

**Status**: Ready for testing! 🎉
