from __future__ import annotations
import os
from django.conf import settings
import re
from pathlib import Path
from django.utils.text import slugify
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


SCOPES = ['https://www.googleapis.com/auth/drive.file']


def applicant_drive_filename(
    name: str | None,
    email: str | None,
    document_type: str,
    original_filename: str | None = None,
    default_extension: str = '',
) -> str:
    """Build a safe, readable Drive filename from an applicant name or email."""
    safe_identity = slugify((name or '').strip())
    if not safe_identity:
        safe_identity = slugify((email or '').strip()) or 'applicant'
    safe_document_type = slugify(document_type) or 'document'

    extension = Path(original_filename or '').suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
        extension = default_extension.lower().strip()
        if extension and not extension.startswith('.'):
            extension = f'.{extension}'
        if not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
            extension = ''

    return f'{safe_identity}_{safe_document_type}{extension}'


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


def extract_drive_file_id(url_or_id: str | None) -> str | None:
    """Best-effort extraction of a Google Drive file id from a URL or raw id string."""
    if not url_or_id:
        return None
    value = (url_or_id or '').strip()
    if not value:
        return None
    match = re.search(r"/d/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value
    return None


def drive_delete(file_id_or_url: str | None) -> bool:
    """Delete the specified Drive file by id or URL. Returns True when deletion succeeds."""
    fid = extract_drive_file_id(file_id_or_url)
    if not fid:
        return False
    svc = _drive_service()
    if not svc:
        return False
    try:
        svc.files().delete(fileId=fid, supportsAllDrives=True).execute()
        return True
    except HttpError as exc:
        if getattr(getattr(exc, 'resp', None), 'status', None) == 404:
            return True
        return False
    except Exception:
        return False
