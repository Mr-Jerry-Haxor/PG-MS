# Leave PG Feature - Comprehensive Implementation Plan

## Overview
Complete redesign of the Leave PG functionality with enhanced user experience, advance amount management, and future swap capabilities.

---

## 📋 Requirements Analysis

### 1. **PG User - Initiate Leave**
- Default leave date: Next month's payment day (e.g., if payment day is 13th, show Oct 13, Nov 13, etc.)
- Custom date option via checkbox
- Notice period validation:
  - If (selected_date - current_date) < notice_period_days:
    - Show warning: "No advance amount will be returned"
    - Require acknowledgment checkbox
  - Else: Eligible for advance return
- Leave reason field (optional)
- Cancel leave request before PG admin confirms

### 2. **PG Admin - Leave Management**
- View leave initiated date/time
- View leave reason
- Edit leave date (pencil icon)
- Confirm/Reject leave request
- Display advance eligibility status
- Mark advance amount as returned:
  - Creates expenditure entry (category: "Advance Return")
  - Auto-generates note with booking user details
  - PG admin enters actual amount returned
  - Expenditure persists even if booking deleted

### 3. **Re-Continue Feature**
- Only visible when: `leaving_date >= current_date` (future or today)
- Options:
  - Same room: Verify no conflicting bookings/swaps
  - Change room: Show vacant rooms/beds
- Validation:
  - Check for future bookings on same bed
  - Check for future swaps involving the bed
  - Display conflicts and force room change if needed

### 4. **Future Swap Feature**
- Allow swapping based on confirmed leaving dates
- Example: Room 101 bed 2 leaving next week → Room 203 tenant swaps to 101 bed 2 effective from leaving date
- Validate:
  - Source room/bed must be vacant or have confirmed leaving
  - Target leaving date must be confirmed
  - No overlapping swaps

---

## 🗃️ Database Schema Changes

### New Fields in `Booking` Model
```python
class Booking(TimeStampedModel):
    # Existing fields...
    leaving_date = models.DateField(null=True, blank=True)
    leaving_confirmed_date = models.DateField(null=True, blank=True)
    
    # NEW FIELDS
    leaving_initiated_at = models.DateTimeField(null=True, blank=True, help_text="When user requested to leave")
    leaving_reason = models.TextField(blank=True, help_text="User's reason for leaving")
    advance_eligible = models.BooleanField(default=True, help_text="Eligible for advance refund based on notice period")
    advance_returned = models.BooleanField(default=False, help_text="Advance amount returned by PG admin")
    advance_returned_at = models.DateTimeField(null=True, blank=True)
    advance_returned_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
```

### New Model: `RoomSwap`
```python
class RoomSwap(TimeStampedModel):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('completed', 'Completed'),
    ]
    
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='swaps')
    from_room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='swaps_from')
    from_share_no = models.PositiveSmallIntegerField()
    to_room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='swaps_to')
    to_share_no = models.PositiveSmallIntegerField()
    effective_date = models.DateField(help_text="Date when swap takes effect")
    is_future_swap = models.BooleanField(default=False, help_text="Swap scheduled for future based on leaving date")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reason = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
```

### Update `Expenditure` Model (if needed)
- Ensure it has category field that supports "Advance Return"
- Add booking reference (nullable, for tracking)

---

## 🎯 Implementation Phases

### Phase 1: Database Migrations
1. Add new fields to Booking model
2. Create RoomSwap model
3. Run migrations
4. Add data migration for existing bookings (set leaving_initiated_at from created_at if leaving_date exists)

### Phase 2: PG User Leave Initiation
**Files to Create/Modify:**
- `bookings/forms.py` - Create `LeaveRequestForm`
- `bookings/views.py` - Add `initiate_leave_pg` view
- `templates/bookings/leave_request.html` - Leave request page
- `templates/dashboard.html` - Update to show "Leave PG" button

**Logic Flow:**
1. User clicks "Leave PG" from dashboard
2. Calculate default leave date (next payment day)
3. Show form with:
   - Default date button (quick select)
   - Custom date checkbox + date picker
   - Notice period calculation
   - Warning for no advance return
   - Acknowledgment checkbox (conditional)
   - Reason textarea (optional)
4. On submit:
   - Validate date >= current_date
   - Calculate notice period compliance
   - Set `advance_eligible` flag
   - Set `leaving_initiated_at` to now
   - Save `leaving_date` and `leaving_reason`
   - Create notification for PG admin
5. Allow cancellation if `leaving_confirmed_date` is NULL

**Payment Day Calculation:**
```python
from datetime import date
from dateutil.relativedelta import relativedelta

payment_day = booking.payment_date.day  # e.g., 13
today = date.today()

# Next month's payment day
next_month = today + relativedelta(months=1)
next_payment_date = next_month.replace(day=payment_day)
```

### Phase 3: PG Admin Leave Confirmation
**Files to Modify:**
- `pgadmin/views.py` - Update `pg_confirm_leave` view
- `templates/pgadmin/leaving_requests.html` - Enhanced leave requests page
- Add pencil icon for edit date
- Show initiated date/time, reason, advance eligibility

**Features:**
1. Display table with columns:
   - User, Room/Bed
   - Initiated At (date/time)
   - Requested Leave Date (editable with pencil icon)
   - Reason
   - Advance Eligible (Yes/No badge)
   - Advance Returned (checkbox + amount input)
   - Actions: Confirm, Reject
2. Edit date modal/inline edit
3. Confirm button:
   - Sets `leaving_confirmed_date`
   - Updates RoomShareStatus
   - Sends notification to user
4. Advance return flow:
   - Checkbox "Mark as Returned"
   - Input field for amount
   - Creates Expenditure entry automatically

### Phase 4: Advance Return Management
**Files to Modify:**
- `finance/models.py` - Ensure Expenditure supports advance return
- `pgadmin/views.py` - Add `mark_advance_returned` view
- Create expenditure entry linked to booking

**Expenditure Creation:**
```python
from finance.models import Expenditure

expenditure = Expenditure.objects.create(
    pg=booking.pg,
    category='Advance Return',
    amount=advance_amount,
    date=timezone.now().date(),
    note=f"Advance returned to {booking.user.get_full_name()} (Room {booking.room.room_no}, Bed {booking.share_no})",
    booking=booking  # Add this FK if not exists
)

booking.advance_returned = True
booking.advance_returned_at = timezone.now()
booking.advance_returned_amount = advance_amount
booking.save()
```

### Phase 5: Leaving Requests Page Enhancements
**Display Requirements:**
- Tabs/Sections:
  - Pending Confirmations
  - Confirmed (Advance Not Returned)
  - Confirmed (Advance Returned)
  - Completed (Past leaving date)
- Filters:
  - Advance eligible vs not eligible
  - Date range
- Bulk actions (if needed)

### Phase 6: Re-Continue Feature
**Files to Create:**
- `pgadmin/views.py` - Add `re_continue_booking` view
- `templates/pgadmin/re_continue_modal.html` - Modal for re-continue
- JavaScript for room selection

**Logic Flow:**
1. Button visible only if: `leaving_confirmed_date >= today`
2. Modal options:
   - ○ Continue in same room (Room {room_no}, Bed {share_no})
   - ○ Change room
3. If same room selected:
   ```python
   # Check for future bookings on this bed
   conflicts = Booking.objects.filter(
       room=booking.room,
       share_no=booking.share_no,
       status__in=['pending', 'approved'],
       joining_date__gte=booking.leaving_confirmed_date
   )
   
   # Check for future swaps
   swap_conflicts = RoomSwap.objects.filter(
       to_room=booking.room,
       to_share_no=booking.share_no,
       effective_date__gte=booking.leaving_confirmed_date,
       status='approved'
   )
   
   if conflicts or swap_conflicts:
       # Display conflicts
       # Force change room
   else:
       # Clear leaving dates
       booking.leaving_date = None
       booking.leaving_confirmed_date = None
       booking.save()
   ```
4. If change room:
   - Show available rooms/beds
   - Create new booking or update current

### Phase 7: Future Swap Feature
**Files to Create:**
- `bookings/models.py` - Add RoomSwap model
- `pgadmin/views.py` - Add swap management views
- `templates/pgadmin/swap_request.html` - Swap request page
- Update tenants page to show swap requests

**Validation Logic:**
```python
def validate_future_swap(booking, to_room, to_share_no, effective_date):
    # Target bed must be vacant or have confirmed leaving by effective_date
    target_booking = Booking.objects.filter(
        room=to_room,
        share_no=to_share_no,
        status='approved'
    ).first()
    
    if target_booking:
        if not target_booking.leaving_confirmed_date:
            return False, "Target bed has no confirmed leaving date"
        if target_booking.leaving_confirmed_date > effective_date:
            return False, f"Target bed will be available only from {target_booking.leaving_confirmed_date}"
    
    # Check no overlapping swaps
    existing_swaps = RoomSwap.objects.filter(
        Q(to_room=to_room, to_share_no=to_share_no) |
        Q(booking=booking),
        effective_date=effective_date,
        status='approved'
    )
    
    if existing_swaps.exists():
        return False, "Overlapping swap exists"
    
    return True, None
```

---

## 🎨 UI/UX Design Guidelines

### Mobile Responsive Design
- Use Bootstrap 5 grid system
- Forms: Stack on mobile, side-by-side on desktop
- Modals: Full-width on mobile
- Tables: Horizontal scroll or card view on mobile

### Payment Date Selection (User Leave Request)
```html
<div class="leave-date-selection">
    <h5>Select Leave Date</h5>
    
    <!-- Quick Select - Next Payment Day -->
    <div class="quick-select mb-3">
        <button class="btn btn-primary btn-lg w-100" id="selectNextPaymentDay">
            Leave on Next Payment Day: <strong>{{ next_payment_date }}</strong>
        </button>
        <div class="notice-period-info mt-2 text-success">
            ✓ Notice period satisfied. Eligible for advance return.
        </div>
    </div>
    
    <!-- Custom Date -->
    <div class="custom-date-section">
        <div class="form-check mb-2">
            <input type="checkbox" class="form-check-input" id="customDateCheck">
            <label class="form-check-label" for="customDateCheck">
                Select custom date
            </label>
        </div>
        
        <div id="customDatePicker" style="display: none;">
            <input type="date" class="form-control" id="leavingDate" name="leaving_date" min="{{ tomorrow }}">
            
            <!-- Dynamic Notice Period Warning -->
            <div id="noticePeriodWarning" class="alert alert-warning mt-2" style="display: none;">
                ⚠️ The selected date is within the notice period ({{ notice_period }} days).
                <strong>No advance amount will be returned.</strong>
            </div>
            
            <!-- Acknowledgment for No Advance -->
            <div id="acknowledgeSection" class="mt-2" style="display: none;">
                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="acknowledgeNoAdvance" required>
                    <label class="form-check-label" for="acknowledgeNoAdvance">
                        I understand that no advance amount will be returned
                    </label>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Reason -->
    <div class="mt-3">
        <label for="leavingReason" class="form-label">Reason for leaving (optional)</label>
        <textarea class="form-control" id="leavingReason" name="leaving_reason" rows="3" placeholder="e.g., Relocation, Job change, etc."></textarea>
    </div>
</div>
```

### PG Admin - Leaving Requests Page
```html
<div class="leaving-requests-page">
    <!-- Filters -->
    <div class="filters mb-3">
        <div class="row">
            <div class="col-md-3">
                <select class="form-select" id="advanceFilter">
                    <option value="">All</option>
                    <option value="eligible">Advance Eligible</option>
                    <option value="not-eligible">Not Eligible</option>
                    <option value="returned">Returned</option>
                    <option value="pending">Pending Return</option>
                </select>
            </div>
            <div class="col-md-3">
                <select class="form-select" id="statusFilter">
                    <option value="pending">Pending Confirmation</option>
                    <option value="confirmed">Confirmed</option>
                    <option value="completed">Completed</option>
                </select>
            </div>
        </div>
    </div>
    
    <!-- Requests Table -->
    <div class="table-responsive">
        <table class="table table-hover">
            <thead>
                <tr>
                    <th>Tenant</th>
                    <th>Room/Bed</th>
                    <th>Initiated</th>
                    <th>Leave Date</th>
                    <th>Reason</th>
                    <th>Advance</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {% for booking in leave_requests %}
                <tr>
                    <td>
                        {{ booking.user.get_full_name }}<br>
                        <small class="text-muted">{{ booking.user.email }}</small>
                    </td>
                    <td>{{ booking.room.room_no }} / {{ booking.share_no }}</td>
                    <td>{{ booking.leaving_initiated_at|date:"M d, Y H:i" }}</td>
                    <td>
                        <span id="leaveDate{{ booking.id }}">{{ booking.leaving_date|date:"Y-m-d" }}</span>
                        <button class="btn btn-sm btn-link p-0" onclick="editLeaveDate({{ booking.id }})">
                            <i class="bi bi-pencil"></i>
                        </button>
                    </td>
                    <td><small>{{ booking.leaving_reason|truncatewords:10 }}</small></td>
                    <td>
                        {% if booking.advance_eligible %}
                            <span class="badge bg-success">Eligible</span>
                            {% if not booking.advance_returned %}
                                <button class="btn btn-sm btn-outline-primary mt-1" onclick="markAdvanceReturned({{ booking.id }})">
                                    Mark Returned
                                </button>
                            {% else %}
                                <span class="badge bg-info">Returned (₹{{ booking.advance_returned_amount }})</span>
                            {% endif %}
                        {% else %}
                            <span class="badge bg-secondary">Not Eligible</span>
                        {% endif %}
                    </td>
                    <td>
                        {% if not booking.leaving_confirmed_date %}
                            <button class="btn btn-sm btn-success" onclick="confirmLeave({{ booking.id }})">Confirm</button>
                            <button class="btn btn-sm btn-outline-danger" onclick="rejectLeave({{ booking.id }})">Reject</button>
                        {% else %}
                            <span class="badge bg-success">Confirmed</span>
                            {% if booking.leaving_confirmed_date >= today %}
                                <button class="btn btn-sm btn-outline-warning" onclick="showReContinue({{ booking.id }})">Re-Continue</button>
                            {% endif %}
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
```

---

## 🧪 Edge Cases & Validation

### Leave Request Validation
1. ✅ User cannot request leave if already has pending leave
2. ✅ Leave date must be >= today
3. ✅ Leave date should be after joining_date
4. ✅ Calculate notice period from current date, not from payment date
5. ✅ If user selects past date (somehow), reject with error
6. ✅ If PG is being deleted, handle gracefully

### Notice Period Calculation
```python
from datetime import date, timedelta

def calculate_notice_period_compliance(booking, requested_leave_date):
    notice_period = booking.pg.notice_period  # days
    today = date.today()
    days_diff = (requested_leave_date - today).days
    
    return {
        'compliant': days_diff >= notice_period,
        'days_diff': days_diff,
        'notice_period': notice_period,
        'advance_eligible': days_diff >= notice_period
    }
```

### Re-Continue Edge Cases
1. ✅ User leaves, then immediately re-continues before leaving date
2. ✅ PG admin has already assigned bed to new tenant
3. ✅ Future swap exists for the bed
4. ✅ Rent calculation: If re-continuing, should rent be adjusted?
5. ✅ Multiple re-continue attempts (prevent spam)

### Future Swap Edge Cases
1. ✅ Source bed leaving date changes after swap approved
2. ✅ Source bed re-continues (cancels leaving)
3. ✅ Target user also wants to swap simultaneously
4. ✅ Chain swaps: A→B, B→C (ensure no circular references)
5. ✅ Swap effective date is past due (auto-complete mechanism?)

### Advance Return Edge Cases
1. ✅ Amount returned > advance_paid (warn admin)
2. ✅ Multiple partial returns (support or prevent?)
3. ✅ Expenditure created but booking deleted later (keep expenditure)
4. ✅ User disputes amount (add notes/comments field?)

---

## 📊 Notifications & Audit Trail

### Notifications to Create
1. **User → Admin:** Leave request submitted
2. **Admin → User:** Leave confirmed/rejected
3. **Admin → User:** Advance amount returned
4. **User → Admin:** Leave request cancelled
5. **Admin → User:** Re-continue approved/rejected
6. **Admin → User/Admin:** Future swap approved/rejected

### Audit Trail
- Use existing `log()` function from `core.audit`
- Log all leave-related actions:
  - Leave initiated
  - Leave confirmed/rejected
  - Leave date edited
  - Advance marked as returned
  - Re-continue action
  - Swap created/approved/rejected

---

## 🚀 API Endpoints (URL Structure)

### User Endpoints
- `GET /bookings/leave-request/<booking_id>/` - Show leave request form
- `POST /bookings/leave-request/<booking_id>/` - Submit leave request
- `POST /bookings/cancel-leave/<booking_id>/` - Cancel pending leave request
- `GET /bookings/my-swaps/` - View user's swap requests

### PG Admin Endpoints
- `GET /pgadmin/leaving-requests/` - List all leave requests
- `POST /pgadmin/confirm-leave/<booking_id>/` - Confirm leave
- `POST /pgadmin/reject-leave/<booking_id>/` - Reject leave
- `POST /pgadmin/edit-leave-date/<booking_id>/` - Edit leave date
- `POST /pgadmin/mark-advance-returned/<booking_id>/` - Mark advance returned
- `GET /pgadmin/re-continue/<booking_id>/` - Show re-continue options
- `POST /pgadmin/re-continue/<booking_id>/` - Process re-continue
- `GET /pgadmin/future-swap/create/<booking_id>/` - Create future swap
- `POST /pgadmin/future-swap/approve/<swap_id>/` - Approve swap
- `POST /pgadmin/future-swap/reject/<swap_id>/` - Reject swap

---

## ✅ Testing Checklist

### Unit Tests
- [ ] Notice period calculation accuracy
- [ ] Payment day calculation (handle month-end cases)
- [ ] Advance eligibility logic
- [ ] Expenditure creation on advance return
- [ ] Re-continue validation (conflict detection)
- [ ] Future swap validation

### Integration Tests
- [ ] Complete leave flow (user request → admin confirm → advance return)
- [ ] Leave cancellation before confirmation
- [ ] Re-continue with same room (no conflicts)
- [ ] Re-continue with room change
- [ ] Future swap approval and execution
- [ ] Multiple simultaneous leave requests (different users)

### UI/UX Tests
- [ ] Mobile responsiveness (all forms and tables)
- [ ] Date picker validation
- [ ] Notice period warning appears/hides correctly
- [ ] Acknowledgment checkbox required when needed
- [ ] Edit leave date pencil icon works
- [ ] Re-continue modal displays correctly
- [ ] Room selection in re-continue flow

### Edge Case Tests
- [ ] Leave request on Feb 29 (leap year), payment day 31
- [ ] Re-continue when bed already assigned
- [ ] Future swap when source bed re-continues
- [ ] Advance return amount validation
- [ ] Booking deleted but expenditure persists
- [ ] Multiple leave cancel/re-request attempts

---

## 📝 Documentation Updates Needed

1. **User Guide:**
   - How to request leave
   - Understanding notice period
   - Advance return process
   - How to cancel leave request

2. **Admin Guide:**
   - Managing leave requests
   - Editing leave dates
   - Marking advance as returned
   - Using re-continue feature
   - Approving future swaps

3. **Developer Guide:**
   - Database schema changes
   - New models and relationships
   - API endpoint documentation
   - Webhook/notification flows

---

## 🎯 Implementation Priority

### Phase 1 (High Priority) - Core Leave Functionality
1. Database migrations (new fields in Booking)
2. User leave request flow
3. PG admin leave confirmation
4. Advance return management
5. Basic leaving requests page

### Phase 2 (Medium Priority) - Advanced Features
1. Edit leave date functionality
2. Re-continue feature
3. Enhanced UI for leaving requests page
4. Expenditure integration

### Phase 3 (Future Enhancement) - Swap Features
1. RoomSwap model and migrations
2. Future swap creation
3. Swap approval workflow
4. Swap execution on effective date (cron job?)

---

## 🔄 Migration Strategy

### Database Migration Steps
1. Create migration for Booking model fields
2. Run migration on development
3. Test with existing data
4. Create data migration for historical bookings
5. Backup production database
6. Run migration on production
7. Verify data integrity

### Backward Compatibility
- Existing `leaving_date` and `leaving_confirmed_date` remain functional
- New features optional (default values handle missing data)
- Old leave requests continue to work
- Gradual rollout: Phase 1 doesn't break existing flow

---

## 📞 Next Steps

1. **Review this plan** - Confirm requirements match expectations
2. **Prioritize phases** - Decide which features to implement first
3. **Begin Phase 1** - Start with database migrations
4. **Incremental development** - Build and test each feature separately
5. **User feedback** - Test with real users after Phase 1
6. **Iterate** - Refine based on feedback before Phase 2

---

**Estimated Development Time:**
- Phase 1: 2-3 days
- Phase 2: 2-3 days  
- Phase 3: 3-4 days
- Testing & refinement: 2 days

**Total: ~10-12 days** (with comprehensive testing)

---

Let me know if you'd like me to:
1. Start implementing Phase 1 immediately
2. Modify any requirements
3. Add more edge case handling
4. Focus on specific feature first
