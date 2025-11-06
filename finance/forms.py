from django import forms
from django.contrib.auth import get_user_model
from .models import Fees, Payment, Expenditure, ExpenditureCategory


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
        fields = ["user", "amount", "date", "from_date", "to_date", "status", "mode", "type", "notes", "upi_amount", "cash_amount"]
        widgets = {
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "from_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "to_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "mode": forms.Select(attrs={"class": "form-select"}),
            "type": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "upi_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "cash_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('mode')
        amount = cleaned.get('amount')
        upi_amount = cleaned.get('upi_amount')
        cash_amount = cleaned.get('cash_amount')

        # When mode is UPI+CASH, require both component fields and ensure they sum to total
        if mode == 'upi_cash':
            errors = {}
            if upi_amount is None:
                errors['upi_amount'] = forms.ValidationError('Enter UPI amount for UPI+CASH mode.')
            if cash_amount is None:
                errors['cash_amount'] = forms.ValidationError('Enter cash amount for UPI+CASH mode.')

            # If both present and amount provided, ensure they add up to amount
            if upi_amount is not None and cash_amount is not None and amount is not None:
                try:
                    # Use Decimal arithmetic via the fields (already Decimal)
                    total = upi_amount + cash_amount
                    if total != amount:
                        msg = 'UPI + Cash must equal the total Amount.'
                        errors['upi_amount'] = forms.ValidationError(msg)
                        errors['cash_amount'] = forms.ValidationError(msg)
                except Exception:
                    # Ignore arithmetic exceptions and let field-level validation handle types
                    pass

            if errors:
                raise forms.ValidationError(errors)

        return cleaned


class ExpenditureForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        pg = kwargs.pop('pg', None)
        super().__init__(*args, **kwargs)
        
        # Replace category field with category_custom if PG is provided
        if pg:
            # Get custom categories for this PG
            categories = ExpenditureCategory.objects.filter(pg=pg).order_by('display_order', 'name')
            self.fields['category_custom'] = forms.ModelChoiceField(
                queryset=categories,
                required=False,
                label='Category',
                widget=forms.Select(attrs={"class": "form-select"}),
                empty_label="Select a category..."
            )
            # Show only the category name in options (do not include PG name)
            # ExpenditureCategory.__str__ includes PG name, so override label rendering here.
            self.fields['category_custom'].label_from_instance = lambda obj: obj.name
            # Remove the old category field
            if 'category' in self.fields:
                del self.fields['category']
            
            # Reorder fields to put category_custom first
            field_order = ['category_custom', 'amount', 'date', 'notes']
            self.order_fields(field_order)
    
    class Meta:
        model = Expenditure
        fields = ["category_custom", "amount", "date", "notes"]
        widgets = {
            "category_custom": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }
