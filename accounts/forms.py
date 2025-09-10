from django import forms
from .models import Profile
from django.core.validators import RegexValidator


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["phone"]
        widgets = {
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "+91 9xxxxxxxxx"})
        }


class OnboardingForm(forms.Form):
    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First name"}),
    )
    last_name = forms.CharField(
        label="Last name (surname)",
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name (surname)"}),
    )
    phone = forms.CharField(
        max_length=10,
        required=True,
        validators=[RegexValidator(r"^\d{10}$", message="Enter 10 digits (numbers only)")],
        widget=forms.TextInput(attrs={"class": "form-control", "inputmode": "numeric", "pattern": "\\d{10}", "placeholder": "10-digit phone"}),
    )
    # Selfie removed per updated requirements

