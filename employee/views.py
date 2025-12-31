from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.db import transaction
from decimal import Decimal
from .models import Employee, EmployeeLedger
from .forms import EmployeeForm, EmployeeLedgerForm
from pgadmin.models import PG, PGAdmin, PGAdminPermission


def _is_website_admin(user):
    """Check if user is a website admin or superuser"""
    if getattr(user, 'is_superuser', False):
        return True
    return hasattr(user, 'profile') and getattr(user.profile, 'is_website_admin', False)


def _can_access_employees(user, pg=None):
    """Check if user can access employee management.
    
    Returns:
        tuple: (can_access: bool, accessible_pgs: list or None, can_edit: bool)
    """
    # Website admins can access all and edit
    if _is_website_admin(user):
        return True, None, True  # None means all PGs, True means can edit
    
    # Check if user is a PG admin with employee permission
    pg_admins = PGAdmin.objects.filter(user=user).select_related('permissions', 'pg')
    accessible_pgs = []
    can_edit = False
    
    for pg_admin in pg_admins:
        try:
            if hasattr(pg_admin, 'permissions') and pg_admin.permissions:
                perm = pg_admin.permissions
                if perm.can_view_employees or perm.can_edit_employees:
                    accessible_pgs.append(pg_admin.pg)
                    if perm.can_edit_employees:
                        can_edit = True
        except PGAdminPermission.DoesNotExist:
            continue
    
    if accessible_pgs:
        # If specific PG requested, check if it's in accessible list
        if pg:
            return any(p.id == pg.id for p in accessible_pgs), accessible_pgs, can_edit
        return True, accessible_pgs, can_edit
    
    return False, [], False


def _can_edit_employees(user, pg=None):
    """Check if user can edit employees for a specific PG.
    
    Returns:
        bool: True if user can create/edit/delete employees
    """
    # Website admins can edit all
    if _is_website_admin(user):
        return True
    
    # Check if user is a PG admin with edit employee permission
    pg_admins = PGAdmin.objects.filter(user=user).select_related('permissions', 'pg')
    
    for pg_admin in pg_admins:
        try:
            if hasattr(pg_admin, 'permissions') and pg_admin.permissions and pg_admin.permissions.can_edit_employees:
                if pg is None or pg_admin.pg.id == pg.id:
                    return True
        except PGAdminPermission.DoesNotExist:
            continue
    
    return False


def employee_access_required(view_func):
    """Decorator to check if user can access employee management"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please login to access this page.')
            return redirect('login')
        
        can_access, accessible_pgs, can_edit = _can_access_employees(request.user)
        if not can_access:
            messages.error(request, 'You do not have permission to access employee management.')
            return redirect('dashboard')
        
        # Store accessible PGs in request for filtering
        request.accessible_pgs = accessible_pgs
        request.is_full_access = accessible_pgs is None  # None means website admin
        request.can_edit_employees = can_edit
        
        return view_func(request, *args, **kwargs)
    return wrapper


def employee_edit_required(view_func):
    """Decorator to check if user can edit employees"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please login to access this page.')
            return redirect('login')
        
        # Get the employee's PG if pk is provided
        pk = kwargs.get('pk') or kwargs.get('employee_pk')
        pg = None
        if pk:
            try:
                from .models import Employee
                emp = Employee.objects.select_related('pg').get(pk=pk)
                pg = emp.pg
            except Employee.DoesNotExist:
                pass
        
        if not _can_edit_employees(request.user, pg):
            messages.error(request, 'You do not have permission to edit employees.')
            return redirect('employee_list')
        
        return view_func(request, *args, **kwargs)
    return wrapper


def website_admin_required(view_func):
    """Decorator to check if user is website admin (for write operations)"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please login to access this page.')
            return redirect('login')
        
        if not _is_website_admin(request.user):
            messages.error(request, 'Only website administrators can perform this action.')
            return redirect('dashboard')
        
        return view_func(request, *args, **kwargs)
    return wrapper


@employee_access_required
def employee_list(request):
    """List all employees with search and filter"""
    employees = Employee.objects.select_related('pg').prefetch_related('ledger_entries')
    
    # Filter by accessible PGs (for PG admins with limited access)
    if not getattr(request, 'is_full_access', False) and getattr(request, 'accessible_pgs', None):
        employees = employees.filter(pg__in=request.accessible_pgs)
    
    # Search
    search_query = request.GET.get('search', '').strip()
    if search_query:
        employees = employees.filter(
            Q(name__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(pg__name__icontains=search_query)
        )
    
    # Filter by PG
    pg_filter = request.GET.get('pg', '').strip()
    if pg_filter:
        employees = employees.filter(pg_id=pg_filter)
    
    # Filter by status
    status_filter = request.GET.get('status', '').strip()
    if status_filter == 'active':
        employees = employees.filter(is_active=True)
    elif status_filter == 'inactive':
        employees = employees.filter(is_active=False)
    
    # Add balance to each employee
    for emp in employees:
        emp.balance = emp.get_ledger_balance()
    
    # Get PGs for filter dropdown (limited to accessible PGs for non-full access users)
    if getattr(request, 'is_full_access', False):
        pgs = PG.objects.all()
    else:
        pgs = request.accessible_pgs or []
    
    context = {
        'employees': employees,
        'pgs': pgs,
        'search_query': search_query,
        'pg_filter': pg_filter,
        'status_filter': status_filter,
        'is_full_access': getattr(request, 'is_full_access', False),
        'can_edit_employees': getattr(request, 'can_edit_employees', False),
    }
    return render(request, 'employee/employee_list.html', context)


@employee_edit_required
def employee_create(request):
    """Create a new employee"""
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                with transaction.atomic():
                    employee = form.save()
                    messages.success(request, f'Employee {employee.name} created successfully.')
                    return redirect('employee_detail', pk=employee.pk)
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for error in errors:
                        messages.error(request, f'{field}: {error}')
            except Exception as e:
                messages.error(request, f'Error creating employee: {str(e)}')
    else:
        form = EmployeeForm()
    
    context = {'form': form, 'action': 'Create'}
    return render(request, 'employee/employee_form.html', context)


@employee_access_required
def employee_detail(request, pk):
    """View employee details and ledger"""
    employee = get_object_or_404(Employee.objects.select_related('pg'), pk=pk)
    
    # Check if user has access to this employee's PG
    if not getattr(request, 'is_full_access', False):
        accessible_pgs = getattr(request, 'accessible_pgs', [])
        if not any(p.id == employee.pg.id for p in accessible_pgs):
            messages.error(request, 'You do not have permission to view this employee.')
            return redirect('employee_list')
    
    ledger_entries = employee.ledger_entries.select_related('created_by').all()
    
    # Calculate balance
    balance = employee.get_ledger_balance()
    
    # Calculate totals by type
    totals = ledger_entries.aggregate(
        total_advances=Sum('amount', filter=Q(transaction_type='advance')),
        total_salary=Sum('amount', filter=Q(transaction_type='salary')),
        total_deductions=Sum('amount', filter=Q(transaction_type='deduction')),
        total_bonus=Sum('amount', filter=Q(transaction_type='bonus')),
    )
    
    context = {
        'employee': employee,
        'ledger_entries': ledger_entries,
        'balance': balance,
        'totals': totals,
        'can_edit_employees': _can_edit_employees(request.user, employee.pg),
    }
    return render(request, 'employee/employee_detail.html', context)


@employee_edit_required
def employee_update(request, pk):
    """Update employee details"""
    employee = get_object_or_404(Employee, pk=pk)
    
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    messages.success(request, f'Employee {employee.name} updated successfully.')
                    return redirect('employee_detail', pk=employee.pk)
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for error in errors:
                        messages.error(request, f'{field}: {error}')
            except Exception as e:
                messages.error(request, f'Error updating employee: {str(e)}')
    else:
        form = EmployeeForm(instance=employee)
    
    context = {'form': form, 'action': 'Update', 'employee': employee}
    return render(request, 'employee/employee_form.html', context)


@employee_edit_required
def employee_delete(request, pk):
    """Delete an employee"""
    employee = get_object_or_404(Employee, pk=pk)
    
    if request.method == 'POST':
        name = employee.name
        employee.delete()
        messages.success(request, f'Employee {name} deleted successfully.')
        return redirect('employee_list')
    
    context = {'employee': employee}
    return render(request, 'employee/employee_confirm_delete.html', context)


@employee_edit_required
def ledger_add(request, employee_pk):
    """Add a ledger entry for an employee"""
    employee = get_object_or_404(Employee, pk=employee_pk)
    
    if request.method == 'POST':
        form = EmployeeLedgerForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    ledger = form.save(commit=False)
                    ledger.employee = employee
                    ledger.created_by = request.user
                    
                    # Validation: Check amount is not zero
                    if ledger.amount == 0:
                        raise ValidationError("Amount cannot be zero.")
                    
                    # Save will auto-adjust amount sign based on transaction type
                    ledger.save()
                    
                    messages.success(request, 'Ledger entry added successfully.')
                    
                    # AJAX response
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'ok': True,
                            'balance': float(employee.get_ledger_balance())
                        })
                    
                    return redirect('employee_detail', pk=employee.pk)
            except ValidationError as e:
                error_msg = str(e) if isinstance(e, str) else '; '.join(e.messages)
                messages.error(request, f'Validation error: {error_msg}')
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'ok': False, 'error': error_msg})
            except Exception as e:
                messages.error(request, f'Error adding ledger entry: {str(e)}')
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'ok': False, 'error': str(e)})
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'errors': form.errors})
    else:
        form = EmployeeLedgerForm()
    
    context = {'form': form, 'employee': employee}
    return render(request, 'employee/ledger_form.html', context)


@employee_edit_required
def ledger_delete(request, pk):
    """Delete a ledger entry"""
    ledger = get_object_or_404(EmployeeLedger.objects.select_related('employee'), pk=pk)
    employee = ledger.employee
    
    if request.method == 'POST':
        ledger.delete()
        messages.success(request, 'Ledger entry deleted successfully.')
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'ok': True,
                'balance': employee.get_ledger_balance()
            })
        
        return redirect('employee_detail', pk=employee.pk)
    
    context = {'ledger': ledger, 'employee': employee}
    return render(request, 'employee/ledger_confirm_delete.html', context)
