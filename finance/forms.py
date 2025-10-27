from django import forms
from django.contrib.auth import get_user_model
from .models import Fees, Payment, Expenditure


class UserChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, **kwargs):
        self.room_map = kwargs.pop('room_map', {})
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        room_no = self.room_map.get(obj.id, '—')
        first = (obj.first_name or '').strip()
        last = (obj.last_name or '').strip()
        name = (first + ('_' if first and last else '') + last) or obj.username
        return f"{room_no} - {name}"


class FeesForm(forms.ModelForm):
    class Meta:
        model = Fees
        fields = ["share_type", "monthly_fee", "advance_amount"]
        widgets = {
            "share_type": forms.Select(attrs={"class": "form-select"}),
            "monthly_fee": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "advance_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }


class PaymentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user_queryset = kwargs.pop('user_queryset', None)
        room_map = kwargs.pop('room_map', None)
        super().__init__(*args, **kwargs)
        # Replace the user field with our labeled, limited choices
        if user_queryset is not None:
            self.fields['user'] = UserChoiceField(
                queryset=user_queryset,
                widget=forms.Select(attrs={"class": "form-select"}),
                room_map=room_map or {},
                required=True,
            )

    class Meta:
        model = Payment
        fields = ["user", "amount", "date", "from_date", "to_date", "status", "mode", "type", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "from_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "to_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "mode": forms.Select(attrs={"class": "form-select"}),
            "type": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class ExpenditureForm(forms.ModelForm):
    class Meta:
        model = Expenditure
        fields = ["category", "amount", "date", "notes"]
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }
