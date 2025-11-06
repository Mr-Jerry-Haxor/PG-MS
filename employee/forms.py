from django import forms
from .models import Employee, EmployeeLedger


class EmployeeForm(forms.ModelForm):
    """Form for creating and updating employees"""
    
    class Meta:
        model = Employee
        fields = [
            'name', 'phone', 'emergency_contact', 'selfie', 'aadhaar',
            'salary', 'joining_date', 'salary_date', 'work_notes', 'pg', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full Name'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+919876543210'}),
            'emergency_contact': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+919876543210'}),
            'selfie': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'aadhaar': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*,.pdf'}),
            'salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'joining_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'salary_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'work_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Job responsibilities, notes, etc.'}),
            'pg': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class EmployeeLedgerForm(forms.ModelForm):
    """Form for adding ledger entries"""
    
    class Meta:
        model = EmployeeLedger
        fields = ['transaction_type', 'amount', 'date', 'description']
        widgets = {
            'transaction_type': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Transaction notes...'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set default date to today
        if not self.instance.pk:
            from datetime import date
            self.fields['date'].initial = date.today()
