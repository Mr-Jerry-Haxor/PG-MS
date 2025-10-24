from django import forms
from .models import ResidentApplication


class MultiFileInput(forms.ClearableFileInput):
    # Enable multiple file selection for this input
    allow_multiple_selected = True


class MultiFileField(forms.Field):
    widget = MultiFileInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('required', False)
        super().__init__(*args, **kwargs)

    def to_python(self, data):
        # Normalize to a list of UploadedFile (or empty list)
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return list(data)
        return [data]

    def validate(self, value):
        # Basic required check; detailed validation is in form.clean_*()
        if self.required and not value:
            raise forms.ValidationError(self.error_messages.get('required', 'This field is required.'))


class ResidentApplicationForm(forms.ModelForm):
    selfie = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    # Accept multiple files for other card: either 1 PDF or up to 2 images
    aadhaar_pdf = MultiFileField(
        required=False,
        widget=MultiFileInput(attrs={
            "class": "form-control",
            "accept": "application/pdf,image/*",
            "multiple": True,
        })
    )

    class Meta:
        model = ResidentApplication
        fields = [
            'name','dob','age','phone','whatsapp_number','email','father_name','father_phone','mother_name','mother_phone','address',
            'date_of_admission','food_pref','marital_status','education','occupation','org_name','org_address',
            'aadhaar_number','has_vehicle','vehicle_number','vehicle_model',
        ]
        widgets = {k: forms.TextInput(attrs={"class":"form-control"}) for k in ['name','phone','whatsapp_number','email','father_name','father_phone','mother_name','mother_phone','education','org_name','aadhaar_number','vehicle_number','vehicle_model']}
        widgets.update({
            'dob': forms.DateInput(attrs={"type":"date","class":"form-control"}),
            'age': forms.NumberInput(attrs={"class":"form-control", "readonly": "readonly"}),
            'address': forms.Textarea(attrs={"class":"form-control", "rows":3}),
            'date_of_admission': forms.DateInput(attrs={"type":"date","class":"form-control"}),
            'food_pref': forms.Select(attrs={"class":"form-select"}),
            'marital_status': forms.Select(attrs={"class":"form-select"}),
            'occupation': forms.Select(attrs={"class":"form-select"}),
            'org_address': forms.Textarea(attrs={"class":"form-control", "rows":3}),
            'has_vehicle': forms.CheckboxInput(attrs={"class":"form-check-input"}),
        })

    def clean_aadhaar_pdf(self):
        # Prefer normalized cleaned value from custom field, fallback to FILES list
        files = self.cleaned_data.get('aadhaar_pdf') or self.files.getlist('aadhaar_pdf') or []
        if not files:
            return None
        # Rule: either exactly 1 PDF, or 1-2 images. Mixed types not allowed.
        imgs = []
        pdfs = []
        for f in files:
            name = (getattr(f, 'name', '') or '').lower()
            ctype = getattr(f, 'content_type', '') or ''
            if ctype == 'application/pdf' or name.endswith('.pdf'):
                pdfs.append(f)
            elif ctype.startswith('image/') or any(name.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp')):
                imgs.append(f)
            else:
                raise forms.ValidationError('Only PDF or image files are allowed (PDF, JPG, PNG, WEBP).')
        if pdfs and imgs:
            raise forms.ValidationError('Please upload either a single PDF or up to two images, not both.')
        if pdfs:
            if len(pdfs) != 1:
                raise forms.ValidationError('Upload exactly one PDF.')
            # Optional: basic encrypted check best-effort
            try:
                import PyPDF2  # type: ignore
                reader = PyPDF2.PdfReader(pdfs[0])
                if getattr(reader, 'is_encrypted', False):
                    raise forms.ValidationError('Password-protected PDFs are not allowed.')
            except Exception:
                pass
            return pdfs
        # Images path
        if not (1 <= len(imgs) <= 2):
            raise forms.ValidationError('Upload one or two images (front/back).')
        return imgs

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make all included fields required
        for name, field in self.fields.items():
            # keep file fields handled by view on create
            if name in ('selfie', 'aadhaar_pdf', 'vehicle_number', 'vehicle_model', 'has_vehicle'):
                continue
            field.required = True
        # Age should be readonly; auto-calculated from DOB
        if 'age' in self.fields:
            self.fields['age'].widget.attrs['readonly'] = 'readonly'
        # Email should be read-only and required
        if 'email' in self.fields:
            self.fields['email'].widget.attrs['readonly'] = 'readonly'
        # Date of admission is now read-only (set from booking)
        if 'date_of_admission' in self.fields:
            self.fields['date_of_admission'].widget.attrs['readonly'] = 'readonly'
            # Prevent native date picker opening (some browsers) while allowing value submission
            self.fields['date_of_admission'].widget.attrs['onfocus'] = 'this.blur()'
        # Update label for mother_phone
        if 'mother_phone' in self.fields:
            self.fields['mother_phone'].label = 'Mother Phone or Emergency Contact 2'
        if 'father_phone' in self.fields:
            self.fields['father_phone'].label = 'Father Phone or Emergency Contact 1'
        # Input constraints for phone and Aadhaar (client hint; server validates too)
        for p in ('phone','father_phone','mother_phone'):
            if p in self.fields:
                self.fields[p].widget.attrs.update({
                    'pattern': r'\d{10}',
                    'maxlength': '10',
                    'inputmode': 'numeric',
                    'placeholder': '10-digit mobile number',
                })
        if 'aadhaar_number' in self.fields:
            self.fields['aadhaar_number'].widget.attrs.update({
                'pattern': r'\d{12}',
                'maxlength': '12',
                'inputmode': 'numeric',
                'placeholder': '12-digit Aadhaar number',
            })

    def clean(self):
        data = super().clean()
        # Validate vehicle details
        if data.get('has_vehicle'):
            if not data.get('vehicle_number'):
                self.add_error('vehicle_number', 'Required when you have a vehicle.')
            if not data.get('vehicle_model'):
                self.add_error('vehicle_model', 'Required when you have a vehicle.')
        # DOB must be before 2010-01-01
        import datetime as _dt
        dob = data.get('dob')
        if dob is None:
            self.add_error('dob', 'Date of Birth is required.')
        else:
            cutoff = _dt.date(2010, 1, 1)
            if not isinstance(dob, _dt.date):
                self.add_error('dob', 'Enter a valid date.')
            elif dob >= cutoff:
                self.add_error('dob', 'DOB must be before year 2010.')
        # Compute age from DOB (as of today) and force-readonly value
        if isinstance(dob, _dt.date):
            today = _dt.date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            data['age'] = age
            # Push into field to ensure instance receives it
            if 'age' in self.fields:
                self.cleaned_data['age'] = age
        # Phone number validations: exactly 10 digits
        import re as _re
        def _normalize_10(name):
            val = (data.get(name) or '').strip()
            digits = ''.join(ch for ch in val if ch.isdigit())
            if len(digits) != 10:
                self.add_error(name, 'Enter a valid 10-digit mobile number.')
            else:
                self.cleaned_data[name] = digits
        for p in ('phone','father_phone','mother_phone'):
            if p in self.fields:
                _normalize_10(p)
        # Aadhaar number: exactly 12 digits
        aadhaar = (data.get('aadhaar_number') or '').strip()
        if aadhaar:
            aadhaar_digits = ''.join(ch for ch in aadhaar if ch.isdigit())
            if len(aadhaar_digits) != 12:
                self.add_error('aadhaar_number', 'Enter a valid 12-digit Aadhaar number.')
            else:
                self.cleaned_data['aadhaar_number'] = aadhaar_digits
        return data
