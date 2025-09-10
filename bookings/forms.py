from django import forms
from .models import Room, Booking
from django.utils import timezone


class BookingRequestForm(forms.Form):
    joining_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        required=True,
    )
from django import forms


class AadhaarForm(forms.Form):
    aadhaar_number = forms.CharField(max_length=20, widget=forms.TextInput(attrs={"class": "form-control"}))
    aadhaar_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
