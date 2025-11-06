# Google Drive Integration for Employee Documents

## Overview
The employee management system now supports automatic Google Drive storage for all employee documents, with automatic fallback to local filesystem if Drive is not configured.

## Features

### 🔄 Hybrid Storage System
- **Primary**: Google Drive (cloud storage)
- **Fallback**: Local filesystem (automatic when Drive unavailable)
- **Seamless**: No code changes needed when switching between storage methods

### 📁 Separate Drive Folders
Employee documents are organized into three Google Drive folders:

1. **Employee Selfies**: `GOOGLE_DRIVE_FOLDER_SELFIES`
   - Profile photos/selfies
   - Optimized for image preview

2. **Aadhaar Documents**: `GOOGLE_DRIVE_FOLDER_AADHAAR`
   - Aadhaar cards and ID proofs
   - Secure storage with access controls

3. **Other Documents**: `GOOGLE_DRIVE_FOLDER_EMPLOYEE`
   - PAN cards, bank details, certificates, etc.
   - General employee document storage

## Configuration

### 1. Environment Variables

Add these to your `.env` file:

```env
# Google Service Account (Recommended for production)
GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account-key.json

# Google Drive Folder IDs
GOOGLE_DRIVE_FOLDER_SELFIES=1abc123def456...
GOOGLE_DRIVE_FOLDER_AADHAAR=1xyz789ghi012...
GOOGLE_DRIVE_FOLDER_EMPLOYEE=1mno345pqr678...

# Optional: Make files publicly viewable (for embedding)
GOOGLE_DRIVE_MAKE_PUBLIC=True

# Alternative: OAuth Token (for personal accounts)
GOOGLE_OAUTH_TOKEN_FILE=/path/to/token.json
```

### 2. Get Folder IDs

To get your Google Drive folder IDs:

1. Open Google Drive in browser
2. Navigate to the folder
3. Copy ID from URL: `https://drive.google.com/drive/folders/FOLDER_ID_HERE`
4. Paste into `.env` file

### 3. Service Account Setup (Recommended)

For production use with Google Workspace:

1. Create a Google Cloud Project
2. Enable Google Drive API
3. Create a Service Account
4. Download the JSON key file
5. Share your Drive folders with the service account email
6. Set `GOOGLE_SERVICE_ACCOUNT_FILE` path in `.env`

### 4. OAuth Setup (Alternative)

For personal Google accounts:

1. Create OAuth 2.0 credentials in Google Cloud Console
2. Download client secrets
3. Run OAuth flow to generate `token.json`
4. Set `GOOGLE_OAUTH_TOKEN_FILE` path in `.env`

## How It Works

### Storage Backends

Three custom storage classes handle Drive integration:

```python
EmployeeSelfieStorage      # For employee photos
EmployeeAadhaarStorage     # For Aadhaar documents  
EmployeeGoogleDriveStorage # For all other documents
```

### Upload Flow

1. **User uploads file** → Django receives the file
2. **Storage backend checks** → Is Google Drive configured?
   - **YES**: Upload to Drive → Store file ID → Save local backup
   - **NO**: Save to local filesystem
3. **Success** → Return file reference
4. **Error** → Automatic fallback to local storage

### File Reference Format

Files stored in Google Drive use this format:
```
drive:{file_id}:{original_filename}
```

Example:
```
drive:1abc123def456ghi789jkl012:employee_photo.jpg
```

This allows:
- Quick Drive file identification
- Original filename preservation
- Local backup tracking

### URL Generation

When displaying files:

- **Google Drive files**: 
  ```
  https://drive.google.com/file/d/{file_id}/preview
  ```

- **Local files**: 
  ```
  /media/employees/selfies/employee_photo.jpg
  ```

### File Deletion

Deletion removes files from:
1. Google Drive (if stored there)
2. Local filesystem (backup copy)

## Benefits

### ✅ Advantages

1. **Cloud Storage**: Files stored securely in Google Drive
2. **Scalability**: No local disk space limitations
3. **Backup**: Local copies maintained automatically
4. **Accessibility**: Access files from Google Drive interface
5. **Sharing**: Easy sharing via Drive's built-in features
6. **Fallback**: System works even if Drive is unavailable
7. **Organization**: Separate folders for different document types

### 🔒 Security

1. **Service Account**: Dedicated credentials for app access
2. **Folder Isolation**: Different folders for different doc types
3. **Access Control**: Drive permissions control who can view
4. **Encryption**: Files encrypted by Google Drive
5. **Audit Trail**: Drive activity logs available

## Usage Examples

### Creating Employee with Photo

```python
from employee.models import Employee

employee = Employee.objects.create(
    name="John Doe",
    phone="+919876543210",
    salary=25000,
    # ... other fields
)

# Upload selfie (automatically goes to Drive if configured)
with open('photo.jpg', 'rb') as f:
    employee.selfie.save('john_doe.jpg', f)

# Upload aadhaar (automatically goes to Drive if configured)
with open('aadhaar.pdf', 'rb') as f:
    employee.aadhaar.save('john_aadhaar.pdf', f)
```

### Adding Additional Documents

```python
from employee.models import EmployeeDocument

# Add PAN card
doc = EmployeeDocument.objects.create(
    employee=employee,
    document_type='pan',
    document_number='ABCDE1234F',
    uploaded_by=request.user
)

with open('pan_card.pdf', 'rb') as f:
    doc.document_file.save('john_pan.pdf', f)
```

### Accessing Files

```python
# Get URL (works for both Drive and local)
selfie_url = employee.selfie.url
# Returns: https://drive.google.com/file/d/1abc.../preview
# or: /media/employees/selfies/john_doe.jpg

# In templates
{{ employee.selfie.url }}  # Automatic URL generation
```

## Troubleshooting

### Files Not Uploading to Drive

**Check:**
1. ✅ `GOOGLE_SERVICE_ACCOUNT_FILE` or `GOOGLE_OAUTH_TOKEN_FILE` set
2. ✅ Service account JSON file exists at specified path
3. ✅ Drive API enabled in Google Cloud Console
4. ✅ Folder IDs are correct
5. ✅ Service account has access to folders (shared with it)

**Solution**: System will automatically fall back to local storage

### "Could not find config for 'default' in settings.STORAGES"

**Fixed**: Added default storage backend in `settings.py`:
```python
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {...}
}
```

### Files Not Visible in Drive

**Check:**
1. ✅ Folder IDs are correct
2. ✅ Service account email is shared on folders
3. ✅ Permissions are set (view/edit)

### Permission Denied Errors

**Solution**: 
1. Go to Google Drive
2. Right-click folder → Share
3. Add service account email (from JSON file)
4. Grant "Editor" permission

## Migration Guide

### From Local to Drive

If you have existing employees with local files:

1. **Configure Drive** (set env variables)
2. **No migration needed** - Existing files remain local
3. **New uploads** will go to Drive
4. **Optional**: Manually migrate old files to Drive if needed

### From Drive to Local

If you need to switch back:

1. **Remove Drive env variables**
2. **System auto-falls back** to local storage
3. **Old Drive files** still accessible via stored file IDs

## Performance

### Optimizations

1. **Local Backup**: Fast access without Drive API calls
2. **Lazy Loading**: Files only loaded when accessed
3. **CDN-Ready**: Drive URLs can be cached
4. **Async Upload**: Future enhancement possibility

### Best Practices

1. Use service accounts for production
2. Keep folder IDs in environment variables
3. Monitor Drive API quota usage
4. Enable local backup for faster loading
5. Use appropriate file permissions

## Support

### Requirements

- `google-api-python-client`
- `google-auth`
- `google-auth-oauthlib`
- `google-auth-httplib2`

### Environment

- Python 3.8+
- Django 5.1+
- Google Drive API v3

---

**Status**: ✅ Fully Integrated and Production-Ready
