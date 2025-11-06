from django.db import models
from django.core.validators import RegexValidator, MinValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from pgadmin.models import PG
from .storage import EmployeeSelfieStorage, EmployeeAadhaarStorage, EmployeeGoogleDriveStorage


class Employee(models.Model):
    """Employee model for managing PG staff"""
    
    name = models.CharField(max_length=200)
    phone_regex = RegexValidator(
        regex=r'^\+?1?\d{9,15}$',
        message="Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed."
    )
    phone = models.CharField(validators=[phone_regex], max_length=17)
    emergency_contact = models.CharField(
        validators=[phone_regex], 
        max_length=17, 
        blank=True, 
        null=True,
        help_text="Emergency contact number"
    )
    
    # Document uploads
    selfie = models.ImageField(
        upload_to='employees/selfies/', 
        blank=True, 
        null=True,
        storage=EmployeeSelfieStorage
    )
    aadhaar = models.FileField(
        upload_to='employees/aadhaar/', 
        blank=True, 
        null=True,
        help_text="Aadhaar card document",
        storage=EmployeeAadhaarStorage
    )
    
    # Employment details
    salary = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Monthly salary amount"
    )
    joining_date = models.DateField()
    salary_date = models.DateField(help_text="Monthly salary payment date")
    work_notes = models.TextField(blank=True, null=True, help_text="Work notes, responsibilities, etc.")
    
    # PG assignment
    pg = models.ForeignKey(PG, on_delete=models.CASCADE, related_name='employees')
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True, help_text="Is employee currently working?")
    
    class Meta:
        ordering = ['-is_active', 'name']
        verbose_name = 'Employee'
        verbose_name_plural = 'Employees'
        indexes = [
            models.Index(fields=['pg', 'is_active']),
            models.Index(fields=['phone']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.pg.name}"
    
    def clean(self):
        """Validate employee data"""
        super().clean()
        
        # Validate joining date is not in future
        if self.joining_date and self.joining_date > timezone.now().date():
            raise ValidationError({
                'joining_date': 'Joining date cannot be in the future.'
            })
        
        # Validate salary date is valid day of month
        if self.salary_date:
            if self.salary_date.day > 31 or self.salary_date.day < 1:
                raise ValidationError({
                    'salary_date': 'Salary date must be a valid day of the month (1-31).'
                })
        
        # Validate phone number uniqueness (optional warning)
        if self.phone:
            existing = Employee.objects.filter(phone=self.phone).exclude(pk=self.pk)
            if existing.exists():
                # This is a warning, not a hard error - same phone might be reused
                pass
    
    def save(self, *args, **kwargs):
        """Override save to run validation"""
        self.full_clean()
        super().save(*args, **kwargs)
    
    def get_ledger_balance(self):
        """Calculate current balance (surplus/deficit) from ledger entries"""
        total = self.ledger_entries.aggregate(
            balance=models.Sum('amount')
        )['balance'] or Decimal('0.00')
        return total
    
    def get_monthly_salary_cost(self):
        """Get the monthly cost for this employee"""
        return self.salary
    
    def get_total_paid(self):
        """Get total amount paid to employee"""
        return self.ledger_entries.filter(
            transaction_type__in=['salary', 'bonus']
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')
    
    def get_total_advances(self):
        """Get total advances given"""
        return abs(self.ledger_entries.filter(
            transaction_type='advance'
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00'))
    
    def get_working_days(self):
        """Get number of days employee has been working"""
        if not self.joining_date:
            return 0
        today = timezone.now().date()
        return (today - self.joining_date).days


class EmployeeLedger(models.Model):
    """Ledger for tracking employee advances and salary payments"""
    
    TRANSACTION_TYPES = (
        ('advance', 'Advance Given'),
        ('salary', 'Salary Paid'),
        ('deduction', 'Deduction'),
        ('bonus', 'Bonus'),
        ('adjustment', 'Adjustment'),
    )
    
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='ledger_entries')
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Positive for payments to employee (salary, bonus), Negative for advances/deductions"
    )
    date = models.DateField()
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(
        'auth.User', 
        on_delete=models.SET_NULL, 
        null=True,
        related_name='employee_ledger_entries'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = 'Employee Ledger Entry'
        verbose_name_plural = 'Employee Ledger Entries'
        indexes = [
            models.Index(fields=['employee', '-date']),
            models.Index(fields=['transaction_type', '-date']),
        ]
    
    def __str__(self):
        return f"{self.employee.name} - {self.get_transaction_type_display()} - ₹{self.amount}"
    
    def clean(self):
        """Validate ledger entry"""
        super().clean()
        
        # Validate date is not in future
        if self.date and self.date > timezone.now().date():
            raise ValidationError({
                'date': 'Transaction date cannot be in the future.'
            })
        
        # Validate date is not before employee joining
        if self.employee_id and self.date:
            try:
                employee = Employee.objects.get(pk=self.employee_id)
                if self.date < employee.joining_date:
                    raise ValidationError({
                        'date': f'Transaction date cannot be before employee joining date ({employee.joining_date}).'
                    })
            except Employee.DoesNotExist:
                pass
        
        # Validate amount is not zero
        if self.amount == 0:
            raise ValidationError({
                'amount': 'Amount cannot be zero.'
            })
        
        # Validate transaction type and amount sign consistency
        if self.transaction_type in ['advance', 'deduction'] and self.amount > 0:
            # Will be auto-corrected in save, but can warn here
            pass
        elif self.transaction_type in ['salary', 'bonus'] and self.amount < 0:
            # Will be auto-corrected in save
            pass
    
    def save(self, *args, **kwargs):
        """Override save to ensure amount sign is correct and run validation"""
        # Auto-correct amount sign based on transaction type
        if self.transaction_type in ['advance', 'deduction']:
            self.amount = abs(self.amount) * -1
        elif self.transaction_type in ['salary', 'bonus', 'adjustment']:
            self.amount = abs(self.amount)
        
        self.full_clean()
        super().save(*args, **kwargs)


class EmployeeAttendance(models.Model):
    """Track daily employee attendance"""
    
    STATUS_CHOICES = (
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('half_day', 'Half Day'),
        ('leave', 'Leave'),
        ('holiday', 'Holiday'),
    )
    
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='present')
    check_in_time = models.TimeField(blank=True, null=True)
    check_out_time = models.TimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    marked_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='marked_attendance'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date']
        unique_together = ['employee', 'date']
        verbose_name = 'Employee Attendance'
        verbose_name_plural = 'Employee Attendance Records'
        indexes = [
            models.Index(fields=['employee', '-date']),
            models.Index(fields=['date', 'status']),
        ]
    
    def __str__(self):
        return f"{self.employee.name} - {self.date} - {self.get_status_display()}"
    
    def clean(self):
        """Validate attendance record"""
        super().clean()
        
        # Validate date is not in future
        if self.date and self.date > timezone.now().date():
            raise ValidationError({
                'date': 'Attendance date cannot be in the future.'
            })
        
        # Validate date is not before employee joining
        if self.employee_id and self.date:
            try:
                employee = Employee.objects.get(pk=self.employee_id)
                if self.date < employee.joining_date:
                    raise ValidationError({
                        'date': f'Attendance date cannot be before employee joining date ({employee.joining_date}).'
                    })
            except Employee.DoesNotExist:
                pass
        
        # Validate check_in is before check_out
        if self.check_in_time and self.check_out_time:
            if self.check_in_time >= self.check_out_time:
                raise ValidationError({
                    'check_out_time': 'Check-out time must be after check-in time.'
                })
    
    def save(self, *args, **kwargs):
        """Override save to run validation"""
        self.full_clean()
        super().save(*args, **kwargs)
    
    def get_work_hours(self):
        """Calculate work hours"""
        if self.check_in_time and self.check_out_time:
            from datetime import datetime, timedelta
            today = timezone.now().date()
            check_in = datetime.combine(today, self.check_in_time)
            check_out = datetime.combine(today, self.check_out_time)
            delta = check_out - check_in
            return delta.total_seconds() / 3600  # Convert to hours
        return 0


class EmployeeDocument(models.Model):
    """Manage employee documents with expiry tracking"""
    
    DOCUMENT_TYPES = (
        ('aadhaar', 'Aadhaar Card'),
        ('pan', 'PAN Card'),
        ('driving_license', 'Driving License'),
        ('passport', 'Passport'),
        ('bank_details', 'Bank Account Details'),
        ('education', 'Education Certificate'),
        ('experience', 'Experience Letter'),
        ('police_verification', 'Police Verification'),
        ('medical', 'Medical Certificate'),
        ('other', 'Other'),
    )
    
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    document_file = models.FileField(
        upload_to='employees/documents/',
        storage=EmployeeGoogleDriveStorage
    )
    document_number = models.CharField(max_length=100, blank=True, null=True, help_text="Document ID/Number")
    issue_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    uploaded_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_employee_documents'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Employee Document'
        verbose_name_plural = 'Employee Documents'
        indexes = [
            models.Index(fields=['employee', 'document_type']),
            models.Index(fields=['expiry_date']),
        ]
    
    def __str__(self):
        return f"{self.employee.name} - {self.get_document_type_display()}"
    
    def clean(self):
        """Validate document"""
        super().clean()
        
        # Validate expiry date is after issue date
        if self.issue_date and self.expiry_date:
            if self.expiry_date <= self.issue_date:
                raise ValidationError({
                    'expiry_date': 'Expiry date must be after issue date.'
                })
    
    def save(self, *args, **kwargs):
        """Override save to run validation"""
        self.full_clean()
        super().save(*args, **kwargs)
    
    def is_expired(self):
        """Check if document is expired"""
        if self.expiry_date:
            return self.expiry_date < timezone.now().date()
        return False
    
    def days_until_expiry(self):
        """Get days until expiry"""
        if self.expiry_date:
            delta = self.expiry_date - timezone.now().date()
            return delta.days
        return None
    
    def is_expiring_soon(self, days=30):
        """Check if document is expiring within specified days"""
        days_left = self.days_until_expiry()
        if days_left is not None:
            return 0 <= days_left <= days
        return False

