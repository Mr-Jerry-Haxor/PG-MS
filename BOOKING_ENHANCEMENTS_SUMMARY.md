# Booking Enhancement Implementation Summary

## Changes Implemented

### 1. Show vacant_from Rooms in Booking Flow ✅

**Frontend Changes:**
- **File:** `templates/bookings/_booknow_form.html`
  - Updated room API fetch to include `?include_vacant_from=true`
  - Updated shares API fetch to include `?include_vacant_from=true`
  - JavaScript already handles `available_from` dates and validates joining date constraints

**Backend Changes:**
- **File:** `bookings/views.py`
  - API endpoints `pg_quick_rooms` and `pg_quick_shares` already support `include_vacant_from` parameter
  - Returns beds with status `VACANT_FROM` and their `vacant_from` dates
  - Frontend enforces that joining_date >= vacant_from when such beds are selected

**How It Works:**
- Beds with status `VACANT_FROM` now appear in the booking form
- These beds show "(from YYYY-MM-DD)" next to the bed number
- A ⏳ icon indicates future-dated availability
- When user selects such a bed, the joining date field is automatically constrained:
  - Minimum date = vacant_from date
  - Maximum date = today + 7 days (Book Now window)
- Validation prevents booking before the bed becomes available

---

### 2. Split Aadhaar Document Upload into Two Fields ✅

**Frontend Changes:**

**File:** `templates/bookings/_application_form_fields.html`

**Old Structure:**
```html
<input type="file" name="aadhaar_pdf" multiple accept="application/pdf,image/*" required />
<div class="form-text">Upload one PDF, or one/two images.</div>
```

**New Structure:**
```html
<!-- Document 1: Required -->
<input type="file" name="aadhaar_pdf" id="aadhaarDoc1" accept="application/pdf,image/*" required />
<div class="form-text">Upload front side image or complete PDF</div>
<div id="aadhaarPreview1" class="mt-2"></div>

<!-- Document 2: Optional -->
<input type="file" name="aadhaar_pdf_2" id="aadhaarDoc2" accept="image/*" />
<div class="form-text">Upload back side image (if not using PDF above)</div>
<div id="aadhaarPreview2" class="mt-2"></div>
```

**JavaScript Enhancements:**
- **Separate preview areas** for each document
- **Smart field enabling:**
  - Document 2 is disabled until Document 1 has an image
  - If Document 1 is a PDF, Document 2 is disabled and cleared
  - If Document 1 is an image, Document 2 is enabled for back side upload
- **Validation:**
  - Document 1: Required, accepts PDF or image (max 2MB)
  - Document 2: Optional, accepts only images (max 2MB)
  - If PDF uploaded in Document 1, Document 2 cannot be used
  - Toast notifications for errors
- **PDF preview:**
  - Uses PDF.js to render first page
  - Detects password-protected PDFs and rejects them
  - Link to open full PDF in new tab
- **Selfie gating:** Camera is enabled only when Document 1 is uploaded

**Backend Changes:**

**File:** `bookings/application_forms.py`

**Changes:**
1. Removed `MultiFileField` and `MultiFileInput` classes
2. Split into two separate `FileField`s:
   ```python
   aadhaar_pdf = forms.FileField(required=False, accept="application/pdf,image/*")
   aadhaar_pdf_2 = forms.FileField(required=False, accept="image/*")
   ```
3. Separate validation methods:
   - `clean_aadhaar_pdf()`: Validates Document 1 (PDF or image, max 2MB, no encryption)
   - `clean_aadhaar_pdf_2()`: Validates Document 2 (image only, max 2MB)
   - `clean()`: Cross-field validation (if PDF in Doc 1, reject Doc 2)

**File:** `bookings/views.py`

**Updated 3 Locations:**

1. **Quick Booking (Book Now)** - Lines ~640-690
2. **Quick Booking (Future/Daywise)** - Lines ~914-964  
3. **Application Fill (Edit)** - Lines ~1507-1607

**Old Pattern:**
```python
aadhaar_files = form.cleaned_data.get('aadhaar_pdf') or []
# Loop through files, separate into imgs and pdfs lists
# Upload based on type
```

**New Pattern:**
```python
aadhaar_file_1 = form.cleaned_data.get('aadhaar_pdf')
aadhaar_file_2 = form.cleaned_data.get('aadhaar_pdf_2')

if aadhaar_file_1:
    if is_pdf:
        # Upload PDF to Google Drive
        inst.aadhaar_file_url = preview
        inst.aadhaar_file_url_2 = ''
    else:
        # Upload front image
        inst.aadhaar_file_url = preview1
        
        # Upload back image if provided
        if aadhaar_file_2:
            inst.aadhaar_file_url_2 = preview2
        else:
            inst.aadhaar_file_url_2 = ''
```

**For application_fill (edit flow):**
- Includes `drive_delete()` calls to remove old files when switching:
  - PDF → images: deletes old PDF
  - 2 images → 1 image: deletes second image
  - 2 images → PDF: deletes both old images

---

## Testing Checklist

### Vacant_from Beds:
- [ ] Navigate to Book Now booking form
- [ ] Select a room that has beds with `vacant_from` dates
- [ ] Verify beds show "(from YYYY-MM-DD)" and ⏳ icon
- [ ] Select a vacant_from bed
- [ ] Verify joining date field is constrained (min = vacant_from, max = today+7)
- [ ] Try selecting a date before vacant_from (should be prevented by browser)
- [ ] Select valid date and submit form
- [ ] Verify booking creates successfully

### Aadhaar Document Upload:

**Scenario 1: PDF Upload**
- [ ] Open booking form (Book Now or Application Fill)
- [ ] Upload a PDF to Document 1
- [ ] Verify preview shows first page
- [ ] Verify Document 2 field is disabled and cleared
- [ ] Verify selfie camera button is enabled
- [ ] Submit form
- [ ] Verify application saves with aadhaar_file_url (PDF) and empty aadhaar_file_url_2

**Scenario 2: Two Images**
- [ ] Upload an image (JPEG/PNG) to Document 1
- [ ] Verify preview shows in Document 1 area
- [ ] Verify Document 2 is now enabled
- [ ] Upload second image to Document 2
- [ ] Verify preview shows in Document 2 area
- [ ] Submit form
- [ ] Verify application saves with both aadhaar_file_url (front) and aadhaar_file_url_2 (back)

**Scenario 3: Single Image**
- [ ] Upload image to Document 1 only
- [ ] Leave Document 2 empty
- [ ] Submit form
- [ ] Verify application saves with aadhaar_file_url and empty aadhaar_file_url_2

**Scenario 4: Validation**
- [ ] Try uploading file > 2MB (should show toast error)
- [ ] Try uploading password-protected PDF (should show error)
- [ ] Try uploading PDF to Document 2 (should show error - images only)
- [ ] Try submitting without Document 1 (should show "required" error)

**Scenario 5: Edit Flow**
- [ ] Edit existing application with 2 images
- [ ] Change Document 1 to PDF
- [ ] Verify Document 2 is cleared and disabled
- [ ] Save
- [ ] Verify old images are deleted from Drive, new PDF is saved

---

## Database Schema
No database migrations required. The existing schema already supports:
- `aadhaar_file_url` (CharField) - stores Document 1 URL
- `aadhaar_file_url_2` (CharField) - stores Document 2 URL (optional)

---

## Files Modified

### Templates:
1. `templates/bookings/_booknow_form.html` - Added `?include_vacant_from=true` to API calls
2. `templates/bookings/_application_form_fields.html` - Split document upload into two fields with new JavaScript

### Backend:
3. `bookings/application_forms.py` - Updated form with two separate FileFields and validation
4. `bookings/views.py` - Updated file handling in 3 locations (Book Now, Future/Daywise, Application Fill)

### Scripts Created (can be deleted):
5. `update_aadhaar_handling.py` - Script to update views.py (Book Now/Future sections)
6. `update_application_fill.py` - Script to update views.py (Application Fill section)

---

## User Experience Improvements

### Before:
- Users had to select multiple files in one file picker (confusing)
- No visual separation between front/back documents
- PDF and images mixed in one field
- No clear guidance on what to upload

### After:
- Clear two-field structure: "Document 1" (required) and "Document 2" (optional)
- Explicit labels: "front side image or complete PDF" vs "back side image"
- Smart field management: PDF upload automatically disables back side field
- Separate previews for each document
- Better error messages and validation
- Selfie gating ensures users upload ID first

### Vacant_from Beds - Before:
- Only fully vacant beds shown
- Users couldn't book beds that would become available soon
- Required admin to manually manage future vacancies

### Vacant_from Beds - After:
- Shows all available beds including those with future availability
- Clear indicators (⏳ icon, "from DATE" label)
- Automatic date validation ensures bookings align with availability
- Better utilization of inventory
