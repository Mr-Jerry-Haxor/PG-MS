from django import forms
from .models import ResidentApplication


class ResidentApplicationForm(forms.ModelForm):
    selfie = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    aadhaar_pdf = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

    class Meta:
        model = ResidentApplication
        fields = [
            'name','dob','age','phone','email','father_name','father_phone','mother_name','mother_phone','address',
            'date_of_admission','food_pref','marital_status','education','occupation','org_name','org_address',
            'aadhaar_number','has_vehicle','vehicle_number','vehicle_model',
            'decl_valuables','decl_notice','decl_deposit','decl_truth'
        ]
        widgets = {k: forms.TextInput(attrs={"class":"form-control"}) for k in ['name','phone','email','father_name','father_phone','mother_name','mother_phone','education','org_name','aadhaar_number','vehicle_number','vehicle_model']}
        widgets.update({
            'dob': forms.DateInput(attrs={"type":"date","class":"form-control"}),
            'age': forms.NumberInput(attrs={"class":"form-control"}),
            'address': forms.Textarea(attrs={"class":"form-control", "rows":3}),
            'date_of_admission': forms.DateInput(attrs={"type":"date","class":"form-control"}),
            'food_pref': forms.Select(attrs={"class":"form-select"}),
            'marital_status': forms.Select(attrs={"class":"form-select"}),
            'occupation': forms.Select(attrs={"class":"form-select"}),
            'org_address': forms.Textarea(attrs={"class":"form-control", "rows":3}),
            'has_vehicle': forms.CheckboxInput(attrs={"class":"form-check-input"}),
            'decl_valuables': forms.CheckboxInput(attrs={"class":"form-check-input"}),
            'decl_notice': forms.CheckboxInput(attrs={"class":"form-check-input"}),
            'decl_deposit': forms.CheckboxInput(attrs={"class":"form-check-input"}),
            'decl_truth': forms.CheckboxInput(attrs={"class":"form-check-input"}),
        })

    def clean_aadhaar_pdf(self):
        f = self.files.get('aadhaar_pdf')
        if f and not f.name.lower().endswith('.pdf'):
            raise forms.ValidationError('Upload a PDF file for Aadhaar.')
        if f:
            try:
                import PyPDF2  # type: ignore
                reader = PyPDF2.PdfReader(f)
                if getattr(reader, 'is_encrypted', False):
                    raise forms.ValidationError('Password-protected PDFs are not allowed.')
            except Exception:
                # If PyPDF2 missing or parsing fails, skip strict check here.
                pass
        return f

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make all included fields required
        for name, field in self.fields.items():
            # keep file fields handled by view on create
            if name in ('selfie', 'aadhaar_pdf', 'vehicle_number', 'vehicle_model', 'has_vehicle'):
                continue
            field.required = True
        # Email should be read-only and required
        if 'email' in self.fields:
            self.fields['email'].widget.attrs['readonly'] = 'readonly'
        # Date of admission is now read-only (set from booking)
        if 'date_of_admission' in self.fields:
            self.fields['date_of_admission'].widget.attrs['readonly'] = 'readonly'
            # Prevent native date picker opening (some browsers) while allowing value submission
            self.fields['date_of_admission'].widget.attrs['onfocus'] = 'this.blur()'

    def clean(self):
        data = super().clean()
        if data.get('has_vehicle'):
            if not data.get('vehicle_number'):
                self.add_error('vehicle_number', 'Required when you have a vehicle.')
            if not data.get('vehicle_model'):
                self.add_error('vehicle_model', 'Required when you have a vehicle.')
        return data
