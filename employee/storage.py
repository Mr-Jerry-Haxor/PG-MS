"""
Custom storage backends for employee files using Google Drive
"""
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from core.drive import drive_upload, drive_delete, extract_drive_file_id
import os


class EmployeeGoogleDriveStorage(FileSystemStorage):
    """
    Storage backend for employee documents that saves to Google Drive
    Falls back to local filesystem if Google Drive is not configured
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_drive = bool(
            getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_FILE', '') or 
            getattr(settings, 'GOOGLE_OAUTH_TOKEN_FILE', '')
        )
        self.folder_id = getattr(settings, 'GOOGLE_DRIVE_FOLDER_EMPLOYEE', '')
    
    def _save(self, name, content):
        """
        Save file to Google Drive if configured, otherwise use local filesystem
        """
        if self.use_drive and self.folder_id:
            try:
                # Upload to Google Drive
                content.seek(0)  # Reset file pointer
                result = drive_upload(content, name, self.folder_id)
                if result:
                    file_id, preview_url = result
                    # Store the file_id as the name for retrieval
                    # We'll save metadata locally for fallback
                    local_name = super()._save(name, content)
                    # Return a reference that includes the drive file ID
                    return f"drive:{file_id}:{os.path.basename(local_name)}"
            except Exception as e:
                # Fallback to local storage if Drive upload fails
                print(f"Google Drive upload failed: {e}, falling back to local storage")
        
        # Fallback to local filesystem
        return super()._save(name, content)
    
    def url(self, name):
        """
        Return URL for the file
        For Google Drive files, return the preview URL
        For local files, return the standard media URL
        """
        if name and name.startswith('drive:'):
            # Extract file_id from the stored name
            parts = name.split(':', 2)
            if len(parts) >= 2:
                file_id = parts[1]
                return f"https://drive.google.com/file/d/{file_id}/preview"
        
        # Fallback to local URL
        return super().url(name)
    
    def delete(self, name):
        """
        Delete file from Google Drive and/or local filesystem
        """
        if name and name.startswith('drive:'):
            # Extract file_id and delete from Google Drive
            parts = name.split(':', 2)
            if len(parts) >= 2:
                file_id = parts[1]
                try:
                    drive_delete(file_id)
                except Exception as e:
                    print(f"Google Drive delete failed: {e}")
            
            # Also delete local copy if it exists
            if len(parts) >= 3:
                local_name = parts[2]
                try:
                    super().delete(local_name)
                except Exception:
                    pass
        else:
            # Regular local file deletion
            super().delete(name)
    
    def exists(self, name):
        """
        Check if file exists
        For Google Drive files, we check local metadata
        """
        if name and name.startswith('drive:'):
            # For Drive files, check if we have local metadata
            parts = name.split(':', 2)
            if len(parts) >= 3:
                return super().exists(parts[2])
            return True  # Assume it exists if we have the file_id
        
        return super().exists(name)


class EmployeeSelfieStorage(EmployeeGoogleDriveStorage):
    """Storage for employee selfies - uses GOOGLE_DRIVE_FOLDER_SELFIES if available"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override folder_id with selfies folder if configured
        selfie_folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', '')
        if selfie_folder:
            self.folder_id = selfie_folder


class EmployeeAadhaarStorage(EmployeeGoogleDriveStorage):
    """Storage for employee aadhaar documents - uses GOOGLE_DRIVE_FOLDER_AADHAAR if available"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override folder_id with aadhaar folder if configured
        aadhaar_folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
        if aadhaar_folder:
            self.folder_id = aadhaar_folder
