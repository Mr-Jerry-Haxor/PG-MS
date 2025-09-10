from __future__ import annotations
import os
from django.conf import settings
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


SCOPES = ['https://www.googleapis.com/auth/drive.file']


def _drive_service():
    """
    Returns a Drive API service using either:
    1) Service Account (preferred), or
    2) User OAuth (token.json) if service account file is not configured.
    """
    # 1) Service Account path
    if getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_FILE', None):
        sa_file = settings.GOOGLE_SERVICE_ACCOUNT_FILE
        if os.path.exists(sa_file):
            creds = service_account.Credentials.from_service_account_file(
                sa_file,
                scopes=SCOPES
            )
            return build('drive', 'v3', credentials=creds, cache_discovery=False)
    # 2) User OAuth path (for personal Google accounts without Workspace)
    token_file = getattr(settings, 'GOOGLE_OAUTH_TOKEN_FILE', '')
    if token_file and os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                return None
        return build('drive', 'v3', credentials=creds, cache_discovery=False)
    # No credentials configured
    return None


def drive_upload(file_obj, filename: str, parent_folder_id: str | None = None) -> tuple[str, str] | None:
    """
    Uploads a file to Google Drive. Returns (file_id, preview_url).
    """
    svc = _drive_service()
    if not svc:
        return None
    metadata = {'name': filename}
    if parent_folder_id:
        metadata['parents'] = [parent_folder_id]
    media = MediaIoBaseUpload(file_obj, mimetype='application/octet-stream', resumable=True)
    created = svc.files().create(
        body=metadata,
        media_body=media,
        fields='id',
        supportsAllDrives=True,
    ).execute()
    fid = created.get('id')
    # Optionally make file publicly readable for preview embedding
    if getattr(settings, 'GOOGLE_DRIVE_MAKE_PUBLIC', False):
        try:
            svc.permissions().create(
                fileId=fid,
                body={'type': 'anyone', 'role': 'reader'},
                supportsAllDrives=True,
            ).execute()
        except Exception:
            pass
    preview = f"https://drive.google.com/file/d/{fid}/preview"
    return fid, preview