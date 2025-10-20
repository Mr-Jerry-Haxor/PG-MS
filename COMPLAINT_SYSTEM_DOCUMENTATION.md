# Complaint Management System - Implementation Guide

## Overview

A comprehensive complaint management system for PG (Paying Guest) tenants and administrators. This system allows tenants with active bookings to raise complaints and enables PG admins to efficiently manage, respond to, and resolve issues.

## Features Implemented

### For Tenants (Users)

1. **View My Complaints** (`/accounts/complaints/`)
   - List all complaints raised by the user
   - Filter by status (Open, In Progress, Solved, etc.)
   - Filter by PG (if user has bookings in multiple PGs)
   - View complaint summary with status, priority, and comment count
   - Mobile-responsive card layout

2. **Create New Complaint** (`/accounts/complaints/create/`)
   - Only available to users with active (approved) bookings
   - Select booking/PG where issue occurred
   - Enter complaint title and detailed description
   - Choose category (Maintenance, Cleanliness, Food, WiFi, Electricity, Water, Security, Noise, Other)
   - Set priority level (Low, Medium, High, Urgent)
   - Mobile-first responsive form with visual category selection

3. **View Complaint Detail** (`/accounts/complaints/<id>/`)
   - View full complaint information
   - See all admin responses (non-internal comments)
   - Timeline view of all updates
   - Status and priority badges
   - Mobile-optimized layout

### For PG Admins

1. **Complaint Dashboard** (`/pg/complaints/`)
   - Overview statistics (Total, Open, In Progress, Solved, Urgent)
   - List all complaints from managed PGs
   - Advanced filtering:
     - By PG
     - By status (default: Open)
     - By priority
     - By category
     - By date range
     - Text search (title, description, user name/email)
   - Sorting options (newest first, oldest first, priority)
   - Visual priority indicators (color-coded borders)
   - Clickable rows for quick access
   - Mobile-responsive table with optimized columns

2. **Complaint Management** (`/pg/complaints/<id>/`)
   - View complete complaint details
   - See tenant information and booking details
   - Timeline of all comments (including internal notes)
   - Quick actions sidebar:
     - Update status (Open → In Progress → Solved/Not Solved → Closed)
     - Change priority
     - Add comments/updates
     - Mark comments as internal (not visible to tenant)
   - Real-time updates via AJAX
   - Sticky action panel for easy access
   - Mobile-optimized layout

## Database Schema

### Complaint Model

```python
class Complaint(TimeStampedModel):
    # Relations
    user = ForeignKey(User)  # Tenant who raised the complaint
    pg = ForeignKey(PG)  # PG where complaint was raised
    booking = ForeignKey(Booking, null=True)  # Active booking reference
    
    # Content
    title = CharField(max_length=200)
    description = TextField()
    
    # Classification
    category = CharField(choices=[
        'maintenance', 'cleanliness', 'food', 'wifi',
        'electricity', 'water', 'security', 'noise', 'other'
    ])
    priority = CharField(choices=['low', 'medium', 'high', 'urgent'])
    status = CharField(choices=[
        'open', 'in_progress', 'solved', 'not_solved', 'closed'
    ])
    
    # Resolution tracking
    resolved_at = DateTimeField(null=True)
    resolved_by = ForeignKey(User, null=True)
    
    # Timestamps (from TimeStampedModel)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
```

### ComplaintComment Model

```python
class ComplaintComment(TimeStampedModel):
    complaint = ForeignKey(Complaint)
    user = ForeignKey(User)  # Admin who added the comment
    comment = TextField()
    is_internal = BooleanField(default=False)  # Internal notes
    
    # Timestamps
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
```

## URL Structure

### User URLs (accounts app)
- `GET /accounts/complaints/` - List my complaints
- `GET /accounts/complaints/create/` - Create complaint form
- `POST /accounts/complaints/create/` - Submit new complaint
- `GET /accounts/complaints/<id>/` - View complaint detail

### Admin URLs (pgadmin app)
- `GET /pg/complaints/` - Complaint dashboard
- `GET /pg/complaints/<id>/` - Complaint detail/management
- `POST /pg/complaints/<id>/comment/` - Add comment (AJAX)
- `POST /pg/complaints/<id>/status/` - Update status (AJAX)
- `POST /pg/complaints/<id>/priority/` - Update priority (AJAX)

## UI/UX Features

### Design Principles
- **Mobile-First**: All interfaces optimized for mobile devices
- **Clean & Modern**: Bootstrap 5 with custom styling
- **Intuitive**: Clear navigation and visual hierarchy
- **Responsive**: Adapts seamlessly from mobile to desktop

### Visual Elements
- **Status Badges**: Color-coded (Open=Red, In Progress=Yellow, Solved=Green)
- **Priority Badges**: Visual indicators (Urgent=Red, High=Orange, Medium=Blue, Low=Gray)
- **Category Icons**: Bootstrap Icons for each category
- **Priority Borders**: Left border color-coding on complaint cards
- **Timeline View**: Visual comment timeline with connecting lines
- **Statistics Cards**: Dashboard with quick stats overview
- **Hover Effects**: Smooth transitions and shadows on interactive elements

### Mobile Optimizations
- Stack filters vertically on small screens
- Hide less critical columns on mobile tables
- Touch-friendly button sizes
- Collapsible filter sections
- Optimized font sizes
- Full-width forms on mobile

## Workflow Example

### Tenant Creates Complaint

1. Tenant logs in and navigates to "My Complaints"
2. Clicks "New Complaint" button
3. Selects their active booking
4. Enters title: "WiFi not working in Room 101"
5. Chooses category: "WiFi/Internet"
6. Sets priority: "High"
7. Describes issue: "WiFi has been down for 2 days, affecting work from home"
8. Submits complaint
9. Redirected to complaint detail page
10. Sees confirmation message

### Admin Manages Complaint

1. Admin receives notification (can be implemented)
2. Navigates to Complaint Dashboard
3. Sees complaint in "Open" status with "High" priority
4. Clicks on complaint row
5. Reviews tenant information and description
6. Changes status to "In Progress"
7. Adds comment: "Contacted service provider, technician scheduled for tomorrow"
8. Tenant sees update on their complaint page
9. Next day, admin updates: "WiFi restored and tested"
10. Changes status to "Solved"
11. System records resolution timestamp and admin

## Security Features

- **Authentication Required**: All endpoints require login
- **Authorization Checks**:
  - Tenants can only view their own complaints
  - Admins can only access complaints from their managed PGs
  - Status/priority updates restricted to admins
- **CSRF Protection**: All POST requests use Django CSRF tokens
- **Input Validation**: Server-side validation for all form inputs
- **XSS Prevention**: Django template auto-escaping enabled

## Performance Optimizations

- **Database Indexes**: Created on frequently queried fields (pg+status, user+status, created_at)
- **Select Related**: Eager loading of related objects to prevent N+1 queries
- **Prefetch Related**: Efficient loading of reverse foreign keys (comments)
- **Pagination Ready**: Structure supports pagination for large complaint lists

## Integration Points

### Existing System Integration
- Uses existing `User` model from Django auth
- Links to `PG` model for PG information
- References `Booking` model to verify active bookings
- Extends `TimeStampedModel` for consistent timestamps
- Uses existing `PGAdmin` model for admin authorization

### Future Enhancements (Optional)
1. **Email Notifications**:
   - Notify tenant when admin comments
   - Alert admin for new complaints
   - Daily digest for pending complaints

2. **File Attachments**:
   - Allow tenants to upload photos of issues
   - Support for documents/screenshots

3. **Complaint Analytics**:
   - Resolution time tracking
   - Category-wise complaint trends
   - Admin performance metrics

4. **Automated Actions**:
   - Auto-escalate unresolved urgent complaints
   - Auto-close complaints after certain period
   - SLA (Service Level Agreement) tracking

5. **Push Notifications**:
   - Real-time updates for mobile apps
   - Browser notifications for admins

## Testing Checklist

### User Flows
- ✅ User with active booking can create complaint
- ✅ User without active booking sees error message
- ✅ User can view only their own complaints
- ✅ Complaint detail shows correct information
- ✅ Comments from admin are visible to user
- ✅ Internal admin notes are hidden from user

### Admin Flows
- ✅ Admin can view all complaints from managed PGs
- ✅ Admin cannot access complaints from other PGs
- ✅ Filters work correctly (status, priority, category, date, search)
- ✅ Sorting works correctly
- ✅ Status update works and updates badge
- ✅ Priority update works and updates badge
- ✅ Comment submission works
- ✅ Internal comment toggle works
- ✅ Resolved timestamp records correctly

### UI/UX
- ✅ Mobile responsive on all pages
- ✅ Forms validate properly
- ✅ Error messages display correctly
- ✅ Success messages display correctly
- ✅ Loading states for AJAX operations
- ✅ Badges display correct colors
- ✅ Icons display correctly

## Files Created/Modified

### Models
- `pgadmin/models.py` - Added `Complaint` and `ComplaintComment` models

### Views
- `accounts/complaint_views.py` - User-facing complaint views
- `pgadmin/complaint_views.py` - Admin complaint management views

### URLs
- `accounts/urls.py` - Added user complaint URLs
- `pgadmin/urls.py` - Added admin complaint URLs

### Templates
- `templates/accounts/complaints/my_complaints.html` - User complaint list
- `templates/accounts/complaints/create_complaint.html` - Create complaint form
- `templates/accounts/complaints/complaint_detail.html` - User complaint detail
- `templates/pgadmin/complaints/admin_complaints.html` - Admin dashboard
- `templates/pgadmin/complaints/admin_complaint_detail.html` - Admin complaint management

### Admin
- `pgadmin/admin.py` - Registered complaint models in Django admin

### Migrations
- `pgadmin/migrations/0008_complaint_complaintcomment_and_more.py` - Database migrations

## Usage Instructions

### For Tenants

1. **To raise a complaint**:
   ```
   Navigate to: /accounts/complaints/
   Click: "New Complaint" button
   Fill in: Booking, Title, Category, Priority, Description
   Submit: Click "Submit Complaint"
   ```

2. **To view complaints**:
   ```
   Navigate to: /accounts/complaints/
   Use filters to narrow down (optional)
   Click on any complaint to view details
   ```

### For PG Admins

1. **To view all complaints**:
   ```
   Navigate to: /pg/complaints/
   Default view shows "Open" complaints
   Use filters and search to find specific complaints
   ```

2. **To manage a complaint**:
   ```
   Click on any complaint row
   Use quick actions sidebar:
     - Update status dropdown
     - Update priority dropdown
     - Add comment with/without internal flag
   All updates save automatically
   ```

## Technical Stack

- **Backend**: Django 5.1.1, Python 3.x
- **Frontend**: Bootstrap 5.3.3, Bootstrap Icons 1.11.3
- **JavaScript**: Vanilla JS (no jQuery), Fetch API for AJAX
- **Database**: SQLite (development), PostgreSQL-ready
- **Styling**: Custom CSS with CSS variables for theming

## Support & Maintenance

### Common Issues

1. **"You must have an active booking" error**
   - User doesn't have any approved bookings
   - Solution: Admin must approve at least one booking first

2. **"PG Admin access required" error**
   - User is not registered as a PG admin
   - Solution: Add user to `PGAdmin` model

3. **Complaints not showing**
   - Check filter settings (default is "Open" status)
   - Try "All Status" filter option

### Database Queries

```sql
-- Count complaints by status
SELECT status, COUNT(*) FROM pgadmin_complaint GROUP BY status;

-- Find urgent unresolved complaints
SELECT * FROM pgadmin_complaint 
WHERE priority = 'urgent' AND status IN ('open', 'in_progress');

-- Average resolution time
SELECT AVG(JULIANDAY(resolved_at) - JULIANDAY(created_at)) as avg_days
FROM pgadmin_complaint WHERE resolved_at IS NOT NULL;
```

## Conclusion

This complaint management system provides a complete, production-ready solution for managing tenant complaints in a PG management system. The mobile-first, modern UI ensures excellent user experience across all devices, while the comprehensive filtering and management features give admins powerful tools to efficiently handle issues.

The system is secure, performant, and easily extensible for future enhancements like notifications, attachments, and analytics.
