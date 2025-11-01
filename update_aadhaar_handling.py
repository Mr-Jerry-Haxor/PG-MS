#!/usr/bin/env python3
"""
Script to update aadhaar file handling from multi-file array to two separate fields.
Updates bookings/views.py in multiple locations.
"""

def main():
    file_path = 'bookings/views.py'
    
    # Read the file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern 1: Old file handling code
    old_handling = '''            # Files handling (same rules as application_fill)
            selfie_file = request.FILES.get('selfie')
            aadhaar_files = form.cleaned_data.get('aadhaar_pdf') or []
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            if aadhaar_files:
                imgs, pdfs = [], []
                for f in aadhaar_files:
                    name = (getattr(f, 'name', '') or '').lower()
                    ctype = getattr(f, 'content_type', '') or ''
                    if ctype == 'application/pdf' or name.endswith('.pdf'):
                        pdfs.append(f)
                    elif ctype.startswith('image/') or any(name.endswith(ext) for ext in ('.jpg','.jpeg','.png','.webp')):
                        imgs.append(f)
                folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
                if pdfs:
                    f = pdfs[0]
                    up = drive_upload(f, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                elif imgs:
                    inst.aadhaar_file_url_2 = ''
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    f1 = imgs[0]
                    ext1 = _pick_ext((getattr(f1, 'name', '') or '').lower())
                    up1 = drive_upload(f1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                    if len(imgs) > 1:
                        f2 = imgs[1]
                        ext2 = _pick_ext((getattr(f2, 'name', '') or '').lower())
                        up2 = drive_upload(f2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2'''
    
    # New handling code
    new_handling = '''            # Files handling with two separate Aadhaar fields
            selfie_file = request.FILES.get('selfie')
            aadhaar_file_1 = form.cleaned_data.get('aadhaar_pdf')
            aadhaar_file_2 = form.cleaned_data.get('aadhaar_pdf_2')
            
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            
            # Handle Aadhaar Document 1 (required)
            folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
            if aadhaar_file_1:
                name = (getattr(aadhaar_file_1, 'name', '') or '').lower()
                ctype = getattr(aadhaar_file_1, 'content_type', '') or ''
                is_pdf = ctype == 'application/pdf' or name.endswith('.pdf')
                
                if is_pdf:
                    # Upload PDF
                    up = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                else:
                    # Upload image (front side)
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    ext1 = _pick_ext(name)
                    up1 = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                    
                    # Handle Aadhaar Document 2 (optional - back side)
                    if aadhaar_file_2:
                        name2 = (getattr(aadhaar_file_2, 'name', '') or '').lower()
                        ext2 = _pick_ext(name2)
                        up2 = drive_upload(aadhaar_file_2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2
                    else:
                        inst.aadhaar_file_url_2 = ''
'''
    
    # Count occurrences
    count = content.count(old_handling)
    print(f"Found {count} occurrences of old file handling code")
    
    # Replace all occurrences
    if count > 0:
        new_content = content.replace(old_handling, new_handling)
        
        # Write back
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"✓ Successfully replaced {count} occurrences")
    else:
        print("✗ No occurrences found - pattern may have already been updated or doesn't match exactly")
    
    # Also update the validation error message
    old_msg = "'Aadhaar/Document upload is required.'"
    new_msg = "'Aadhaar Document 1 is required.'"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    msg_count = content.count(old_msg)
    if msg_count > 0:
        content = content.replace(old_msg, new_msg)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Updated {msg_count} validation error messages")

if __name__ == '__main__':
    main()
