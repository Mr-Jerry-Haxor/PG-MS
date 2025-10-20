# Complaint System - Quick Start Guide

## 🚀 Quick Setup (Already Done!)

The complaint management system has been fully implemented and integrated into your PG Management System.

## ✅ What's Included

### Models Created
- ✅ `Complaint` - Stores tenant complaints with status, priority, category
- ✅ `ComplaintComment` - Stores admin responses and updates

### Views Created
- ✅ User views: List complaints, create complaint, view detail
- ✅ Admin views: Dashboard, manage complaints, update status/priority, add comments

### Templates Created
- ✅ Mobile-first responsive UI with Bootstrap 5
- ✅ Modern, clean design with status badges and priority indicators
- ✅ Timeline view for comments
- ✅ Advanced filtering and sorting

### URLs Configured
- ✅ `/accounts/complaints/` - User complaint portal
- ✅ `/pg/complaints/` - Admin complaint management

## 📱 Access URLs

### For Tenants
- **View My Complaints**: http://localhost:8000/accounts/complaints/
- **Create Complaint**: http://localhost:8000/accounts/complaints/create/
- **View Detail**: http://localhost:8000/accounts/complaints/{id}/

### For PG Admins
- **Complaint Dashboard**: http://localhost:8000/pg/complaints/
- **Manage Complaint**: http://localhost:8000/pg/complaints/{id}/

## 🎯 How to Use

### As a Tenant

1. **Create a Complaint**:
   ```
   1. Go to "My Complaints" page
   2. Click "New Complaint" button
   3. Select your booking
   4. Fill in title, category, priority, description
   5. Click "Submit Complaint"
   ```

2. **Track Your Complaints**:
   ```
   1. Go to "My Complaints" page
   2. See all your complaints with status
   3. Click any complaint to view details
   4. Read admin responses in timeline
   ```

### As a PG Admin

1. **View All Complaints**:
   ```
   1. Go to PG Complaints dashboard
   2. See statistics at the top
   3. Use filters to narrow down
   4. Click any row to manage
   ```

2. **Manage a Complaint**:
   ```
   1. Click on complaint from dashboard
   2. Read full details
   3. Use sidebar to:
      - Update status
      - Change priority
      - Add comments/updates
      - Mark notes as internal
   ```

## 🎨 Features Highlights

### Mobile-First Design
- ✅ Fully responsive on all screen sizes
- ✅ Touch-friendly buttons and controls
- ✅ Optimized layouts for mobile
- ✅ Collapsible filters on small screens

### Status Workflow
```
Open → In Progress → Solved/Not Solved → Closed
```

### Priority Levels
- 🔴 **Urgent** - Immediate action needed
- 🟠 **High** - Needs attention soon
- 🔵 **Medium** - Normal priority
- ⚫ **Low** - Can wait

### Categories
- 🔧 Maintenance
- 🧹 Cleanliness
- 🍽️ Food
- 📡 WiFi/Internet
- ⚡ Electricity
- 💧 Water Supply
- 🛡️ Security
- 🔊 Noise
- ❓ Other

## 🔐 Security

- ✅ Users can only see their own complaints
- ✅ Admins can only access complaints from their PGs
- ✅ CSRF protection on all forms
- ✅ Authentication required for all pages
- ✅ Internal notes hidden from users

## 📊 Admin Dashboard Features

### Statistics
- Total complaints count
- Open complaints count
- In Progress count
- Solved complaints count

### Filters
- Filter by PG
- Filter by status (default: Open)
- Filter by priority
- Filter by category
- Date range filter
- Text search

### Sorting
- Newest first
- Oldest first
- Priority (High to Low)
- Priority (Low to High)
- By status

## 🧪 Testing the System

### Test as Tenant

1. **Ensure you have an active booking**:
   ```python
   # In Django shell or admin panel
   # User must have a Booking with status='approved'
   ```

2. **Create a test complaint**:
   ```
   Title: "Test Issue - WiFi Down"
   Category: WiFi/Internet
   Priority: High
   Description: "WiFi not working since yesterday"
   ```

3. **Verify you can see it in "My Complaints"**

### Test as Admin

1. **Ensure you are a PG Admin**:
   ```python
   # In Django shell or admin panel
   # Create PGAdmin record linking user to PG
   ```

2. **Go to Complaint Dashboard**
3. **Click on the test complaint**
4. **Try these actions**:
   - Change status to "In Progress"
   - Update priority to "Urgent"
   - Add a comment
   - Add an internal note

## 🎨 UI Components

### Status Badges
- 🔴 **Open** - Red badge
- 🟡 **In Progress** - Yellow badge
- 🟢 **Solved** - Green badge
- ⚪ **Not Solved** - Gray badge
- ⚫ **Closed** - Dark badge

### Visual Indicators
- Left border color on cards shows priority
- Icons for each category
- Timeline view for comments
- Hover effects on interactive elements
- Smooth transitions throughout

## 📝 Database Schema

### Complaint Fields
```python
- user (ForeignKey to User)
- pg (ForeignKey to PG)
- booking (ForeignKey to Booking)
- title (CharField, max 200)
- description (TextField)
- category (CharField with choices)
- priority (CharField with choices)
- status (CharField with choices)
- resolved_at (DateTimeField, nullable)
- resolved_by (ForeignKey to User, nullable)
- created_at (Auto timestamp)
- updated_at (Auto timestamp)
```

### ComplaintComment Fields
```python
- complaint (ForeignKey to Complaint)
- user (ForeignKey to User)
- comment (TextField)
- is_internal (Boolean, default False)
- created_at (Auto timestamp)
- updated_at (Auto timestamp)
```

## 🔧 Customization

### Change Colors
Edit the CSS in template head sections:
- `.priority-urgent` - Urgent priority color
- `.priority-high` - High priority color
- `.priority-medium` - Medium priority color
- `.priority-low` - Low priority color

### Add New Categories
Edit `pgadmin/models.py`:
```python
CATEGORY_CHOICES = [
    # Add new categories here
    ('new_category', 'Display Name'),
]
```
Then run: `python manage.py makemigrations && python manage.py migrate`

### Modify Status Workflow
Edit `pgadmin/models.py`:
```python
STATUS_CHOICES = [
    # Modify or add statuses here
]
```

## 🐛 Troubleshooting

### "PG Admin access required" error
**Solution**: Add user to PGAdmin model in Django admin

### "You must have an active booking" error
**Solution**: Ensure user has at least one approved booking

### Complaints not showing in admin dashboard
**Solution**: Check that default status filter is not too restrictive. Try "All Status"

### AJAX updates not working
**Solution**: Check browser console for errors. Ensure CSRF token is present in cookies.

## 📚 Additional Resources

- Full Documentation: `COMPLAINT_SYSTEM_DOCUMENTATION.md`
- Model Code: `pgadmin/models.py`
- User Views: `accounts/complaint_views.py`
- Admin Views: `pgadmin/complaint_views.py`
- Templates: `templates/accounts/complaints/` and `templates/pgadmin/complaints/`

## 🎉 You're Ready!

The complaint management system is fully functional and ready to use. Just run your development server and navigate to the URLs above to start using it!

```bash
python manage.py runserver
```

Then visit:
- Tenant portal: http://localhost:8000/accounts/complaints/
- Admin dashboard: http://localhost:8000/pg/complaints/

Enjoy your new complaint management system! 🚀
