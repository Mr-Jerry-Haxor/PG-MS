# Employee Management App

## Overview
The Employee Management System is a dedicated app for managing PG staff members with **Google Drive integration** for secure cloud storage. It is only accessible to **website admins**.

## Features

### Employee Management
- **Add Employees**: Create employee records with personal and employment details
- **Update Employees**: Modify employee information
- **View Employees**: See all employees with filtering and search
- **Delete Employees**: Remove employee records (with confirmation)

### Employee Information
Each employee record includes:
- **Personal Details**:
  - Name
  - Phone number
  - Emergency contact (optional)
  - Selfie/Photo (📁 Google Drive)
  - Aadhaar document (📁 Google Drive)
  
- **Employment Details**:
  - Associated PG
  - Monthly salary
  - Joining date
  - Salary payment date (e.g., 5th of every month)
  - Work notes (responsibilities, special instructions)
  - Active/Inactive status

### Employee Ledger System
Track all financial transactions for each employee:

- **Transaction Types**:
  - **Salary Paid**: Regular salary payments (positive balance)
  - **Advance Given**: Money given to employee in advance (negative balance)
  - **Bonus**: Additional payments (positive balance)
  - **Deduction**: Deductions from salary (negative balance)
  - **Adjustment**: Manual balance adjustments

- **Balance Tracking**:
  - Current balance (surplus/deficit)
  - Total salary paid
  - Total advances given
  - Total bonuses
  - Transaction history with descriptions

### Access Control
- Only **website_admin** users can access the employee system
- Automatically redirects non-admin users to dashboard
- All views are protected with the `@website_admin_required` decorator

## URL Structure

```
/employees/                          - List all employees
/employees/create/                   - Create new employee
/employees/<id>/                     - View employee details & ledger
/employees/<id>/update/              - Update employee
/employees/<id>/delete/              - Delete employee
/employees/<id>/ledger/add/          - Add ledger entry
/employees/ledger/<id>/delete/       - Delete ledger entry
```

## Navigation
Access the employee system from:
- **Site Admin** dropdown menu → **Employees**

## Search & Filter
The employee list supports:
- **Search**: By name, phone, or PG name
- **Filter by PG**: Show employees from specific PG
- **Filter by Status**: Active or Inactive employees

## Ledger Balance Logic
- **Positive Balance**: Employee is owed money (pending payment)
- **Negative Balance**: Employee has received advance (money given ahead)
- **Zero Balance**: All accounts settled

Transaction amount signs:
- Advances & Deductions: Stored as negative (money out)
- Salary, Bonus, Adjustment: Stored as positive (money in)

## Models

### Employee
- Fields: name, phone, emergency_contact, selfie, aadhaar, salary, joining_date, salary_date, work_notes, pg, is_active
- Relationships: Belongs to one PG, has many ledger entries
- Methods: `get_ledger_balance()` - calculates current balance

### EmployeeLedger
- Fields: employee, transaction_type, amount, date, description, created_by
- Relationships: Belongs to one Employee, one User (creator)
- Auto-tracks: Created timestamp

## Admin Panel
Both Employee and EmployeeLedger models are registered in Django admin with:
- List views with filtering
- Search functionality
- Organized fieldsets
- Readonly timestamps

## Templates
- `employee_list.html` - Employee cards with search/filter
- `employee_detail.html` - Employee profile with ledger
- `employee_form.html` - Create/Update form
- `employee_confirm_delete.html` - Delete confirmation

## Google Drive Integration 🔄

### Cloud Storage
All employee documents are automatically uploaded to **Google Drive** when configured:

- **Selfies** → `GOOGLE_DRIVE_FOLDER_SELFIES`
- **Aadhaar** → `GOOGLE_DRIVE_FOLDER_AADHAAR`  
- **Documents** → `GOOGLE_DRIVE_FOLDER_EMPLOYEE`

### Features
- ✅ Automatic cloud upload
- ✅ Local backup for fast access
- ✅ Fallback to filesystem if Drive unavailable
- ✅ Secure service account authentication
- ✅ Organized folder structure
- ✅ Preview URLs for embedded viewing

### Setup
See [GOOGLE_DRIVE_SETUP.md](./GOOGLE_DRIVE_SETUP.md) for detailed configuration.

Quick start:
1. Set `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env`
2. Set folder IDs in `.env`
3. Share folders with service account
4. Upload files - they go to Drive automatically!

## Future Enhancements
Potential features:
- Attendance tracking
- Leave management
- Salary slip generation
- Performance reviews
- Export employee data to Excel/PDF
