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
from pgadmin.models import PG


def website_admin_required(view_func):
    """Decorator to check if user is website admin"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please login to access this page.')
            return redirect('login')
        
        if not hasattr(request.user, 'profile') or not request.user.profile.is_website_admin:
            messages.error(request, 'You do not have permission to access employee management.')
            return redirect('dashboard')
        
        return view_func(request, *args, **kwargs)
    return wrapper


@website_admin_required
def employee_list(request):
    """List all employees with search and filter"""
    employees = Employee.objects.select_related('pg').prefetch_related('ledger_entries')
    
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
    
    pgs = PG.objects.all()
    
    context = {
        'employees': employees,
        'pgs': pgs,
        'search_query': search_query,
        'pg_filter': pg_filter,
        'status_filter': status_filter,
    }
    return render(request, 'employee/employee_list.html', context)


@website_admin_required
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


@website_admin_required
def employee_detail(request, pk):
    """View employee details and ledger"""
    employee = get_object_or_404(Employee.objects.select_related('pg'), pk=pk)
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
    }
    return render(request, 'employee/employee_detail.html', context)


@website_admin_required
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


@website_admin_required
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


@website_admin_required
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


@website_admin_required
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
