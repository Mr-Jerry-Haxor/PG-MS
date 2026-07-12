from django import forms
from .models import PG, WhatsAppCloudConfig
from .whatsapp_crypto import decrypt_secret, encrypt_secret
from bookings.models import Room, RoomShareStatus


class PGForm(forms.ModelForm):
    class Meta:
        model = PG
        fields = ["name", "address", "phone", "referral_amount", "daywise_fee", "past_joining_date_allowed", "allow_custom_leave_date", "notice_period", "whatsapp_invite_link", "whatsapp_invite_message"]
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
            "daywise_fee": forms.NumberInput(attrs={
                "class": "form-control",
                "min": "0",
                "step": "0.01",
                "placeholder": "Fee per day for day-wise bookings (₹)",
            }),
            "past_joining_date_allowed": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "allow_custom_leave_date": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notice_period": forms.NumberInput(attrs={
                "class": "form-control",
                "min": "1",
                "max": "90",
                "placeholder": "Notice period in days",
            }),
            "whatsapp_invite_link": forms.URLInput(attrs={
                "class": "form-control",
                "placeholder": "https://chat.whatsapp.com/...",
            }),
            "whatsapp_invite_message": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Welcome to our PG! Please join our WhatsApp group for updates and communication.",
            }),
        }


class WhatsAppCloudConfigForm(forms.ModelForm):
    access_token = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        help_text='Leave blank to keep the stored token.'
    )
    verify_token = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        help_text='A private token you choose for webhook verification. Leave blank to keep it.'
    )
    app_secret = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        help_text='Used to verify X-Hub-Signature-256. Leave blank to keep it.'
    )

    class Meta:
        model = WhatsAppCloudConfig
        fields = [
            'enabled', 'api_base_url', 'api_version', 'messages_endpoint',
            'phone_number_id', 'business_account_id', 'display_phone_number',
            'template_language', 'monthly_template_name', 'messages_template_name',
            'leaving_template_name', 'compliance_template_name',
            'enable_monthly_dashboard', 'enable_whatsapp_messages',
            'enable_leaving_page', 'enable_compliance_page',
        ]
        widgets = {
            'enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'api_base_url': forms.URLInput(attrs={'class': 'form-control'}),
            'api_version': forms.TextInput(attrs={'class': 'form-control'}),
            'messages_endpoint': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Optional: https://graph.facebook.com/v25.0/{phone_number_id}/messages',
            }),
            'phone_number_id': forms.TextInput(attrs={'class': 'form-control'}),
            'business_account_id': forms.TextInput(attrs={'class': 'form-control'}),
            'display_phone_number': forms.TextInput(attrs={'class': 'form-control'}),
            'template_language': forms.TextInput(attrs={'class': 'form-control'}),
            'monthly_template_name': forms.TextInput(attrs={'class': 'form-control'}),
            'messages_template_name': forms.TextInput(attrs={'class': 'form-control'}),
            'leaving_template_name': forms.TextInput(attrs={'class': 'form-control'}),
            'compliance_template_name': forms.TextInput(attrs={'class': 'form-control'}),
            'enable_monthly_dashboard': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'enable_whatsapp_messages': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'enable_leaving_page': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'enable_compliance_page': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('enabled'):
            required = {
                'phone_number_id': cleaned.get('phone_number_id'),
                'access_token': cleaned.get('access_token') or decrypt_secret(self.instance.access_token_encrypted),
                'verify_token': cleaned.get('verify_token') or decrypt_secret(self.instance.verify_token_encrypted),
                'app_secret': cleaned.get('app_secret') or decrypt_secret(self.instance.app_secret_encrypted),
            }
            for field, value in required.items():
                if not value:
                    self.add_error(field, 'This value is required when Cloud API is enabled.')
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        for form_field, model_field in (
            ('access_token', 'access_token_encrypted'),
            ('verify_token', 'verify_token_encrypted'),
            ('app_secret', 'app_secret_encrypted'),
        ):
            value = self.cleaned_data.get(form_field)
            if value:
                setattr(instance, model_field, encrypt_secret(value))
        if commit:
            instance.save()
        return instance


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["room_no", "total_shares"]
        widgets = {
            "room_no": forms.TextInput(attrs={"class": "form-control"}),
            "total_shares": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 10}),
        }


class ShareStatusForm(forms.ModelForm):
    class Meta:
        model = RoomShareStatus
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
        }
