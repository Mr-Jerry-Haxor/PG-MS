from django import forms
from .models import PG
from bookings.models import Room, RoomShareStatus


class PGForm(forms.ModelForm):
    class Meta:
        model = PG
        fields = ["name", "address", "phone", "referral_amount"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "phone": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Contact phone (e.g. +91 98765 43210)",
                "inputmode": "tel",
                "maxlength": "20",
            }),
            "referral_amount": forms.NumberInput(attrs={
                "class": "form-control",
                "min": "0",
                "step": "0.01",
                "placeholder": "Default referral credit (₹)",
            }),
        }


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["room_no", "total_shares"]
        widgets = {
            "room_no": forms.TextInput(attrs={"class": "form-control"}),
            "total_shares": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 5}),
        }


class ShareStatusForm(forms.ModelForm):
    class Meta:
        model = RoomShareStatus
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
        }
