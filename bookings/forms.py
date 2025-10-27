from django import forms
from .models import Room, Booking
from django.utils import timezone


class BookingRequestForm(forms.Form):
    joining_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        required=True,
    )


class LeaveRequestForm(forms.Form):
    """Form for users to request leaving PG"""
    leaving_date = forms.DateField(
        widget=forms.DateInput(attrs={
            "type": "date", 
            "class": "form-control",
            "id": "leavingDateInput"
        }),
        required=True,
        label="Leave Date"
    )
    leaving_reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "e.g., Relocation, Job change, Personal reasons, etc."
        }),
        required=False,
        label="Reason for leaving (optional)"
    )
    acknowledge_no_advance = forms.BooleanField(
        required=False,
        label="I understand that no advance amount will be returned",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"})
    )
    
    def __init__(self, *args, booking=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.booking = booking


class AadhaarForm(forms.Form):
    aadhaar_number = forms.CharField(max_length=20, widget=forms.TextInput(attrs={"class": "form-control"}))
    aadhaar_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

