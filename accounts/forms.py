from django import forms
from .models import Profile


class ProfileForm(forms.ModelForm):
    # Include User fields for convenient editing on the same form
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

    class Meta:
        model = Profile
        fields = ["phone", "first_name", "last_name"]
        widgets = {
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "+91 9xxxxxxxxx"})
        }


    # OnboardingForm removed; phone can be edited on profile or application pages per new flow.

