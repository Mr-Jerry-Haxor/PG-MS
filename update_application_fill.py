#!/usr/bin/env python3
"""
Script to update aadhaar file handling in application_fill view.
This handles the case where files are edited (with drive_delete).
"""

def main():
    file_path = 'bookings/views.py'
    
    # Read the file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Update the validation check
    old_check = "if app is None and not request.FILES.get('aadhaar_pdf'):"
    new_check = "if app is None and not form.cleaned_data.get('aadhaar_pdf'):"
    
    content = content.replace(old_check, new_check)
    
    # Update error message
    old_err = '"Aadhaar document is required (PDF or Image)."'
    new_err = '"Aadhaar Document 1 is required."'
    
    content = content.replace(old_err, new_err)
    
    # Pattern for the file handling in application_fill
    old_pattern = '''            # Upload files to Drive
            selfie_file = request.FILES.get('selfie')
            # Accept multiple files for Aadhaar/other card: either one PDF or up to two images
            aadhaar_files = form.cleaned_data.get('aadhaar_pdf') or []
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            else:
                # keep existing
                if app:
                    inst.selfie_url = app.selfie_url
            if aadhaar_files:
                # Separate images and PDFs by content type/extension
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
                    # Take the first/only PDF
                    # When modifying: delete old images if switching from images to PDF
                    old_url_1 = app.aadhaar_file_url if app else None
                    old_url_2 = getattr(app, 'aadhaar_file_url_2', None) if app else None
                    
                    f = pdfs[0]
                    up = drive_upload(f, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                        # Delete old files if they're different
                        if old_url_1 and old_url_1 != preview:
                            try:
                                drive_delete(old_url_1)
                            except Exception:
                                pass
                        if old_url_2:
                            try:
                                drive_delete(old_url_2)
                            except Exception:
                                pass
                elif imgs:'''
    
    new_pattern = '''            # Upload files to Drive with two separate Aadhaar fields
            selfie_file = request.FILES.get('selfie')
            aadhaar_file_1 = form.cleaned_data.get('aadhaar_pdf')
            aadhaar_file_2 = form.cleaned_data.get('aadhaar_pdf_2')
            
            if selfie_file:
                up = drive_upload(selfie_file, f"selfie_{request.user.id}", getattr(settings, 'GOOGLE_DRIVE_FOLDER_SELFIES', ''))
                if up:
                    _fid, preview = up
                    inst.selfie_url = preview
            else:
                # keep existing
                if app:
                    inst.selfie_url = app.selfie_url
            
            # Handle Aadhaar Document 1 and optionally Document 2
            folder = getattr(settings, 'GOOGLE_DRIVE_FOLDER_AADHAAR', '')
            if aadhaar_file_1:
                old_url_1 = app.aadhaar_file_url if app else None
                old_url_2 = getattr(app, 'aadhaar_file_url_2', None) if app else None
                
                name = (getattr(aadhaar_file_1, 'name', '') or '').lower()
                ctype = getattr(aadhaar_file_1, 'content_type', '') or ''
                is_pdf = ctype == 'application/pdf' or name.endswith('.pdf')
                
                if is_pdf:
                    # Upload PDF - delete old files if switching from images
                    up = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}.pdf", folder)
                    if up:
                        _fid, preview = up
                        inst.aadhaar_file_url = preview
                        inst.aadhaar_file_url_2 = ''
                        # Delete old files if they're different
                        if old_url_1 and old_url_1 != preview:
                            try:
                                drive_delete(old_url_1)
                            except Exception:
                                pass
                        if old_url_2:
                            try:
                                drive_delete(old_url_2)
                            except Exception:
                                pass
                else:'''
    
    if old_pattern in content:
        content = content.replace(old_pattern, new_pattern)
        print("✓ Updated application_fill file handling pattern")
    else:
        print("✗ Pattern not found in application_fill section")
    
    # Now handle the images path (continuation)
    old_imgs = '''                else:
                    # Upload up to two images as front/back
                    old_url_1 = app.aadhaar_file_url if app else None
                    old_url_2 = getattr(app, 'aadhaar_file_url_2', None) if app else None
                    inst.aadhaar_file_url_2 = ''
                    
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    
                    # Always use first image as front
                    f1 = imgs[0]
                    ext1 = _pick_ext((getattr(f1, 'name', '') or '').lower())
                    up1 = drive_upload(f1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                        # Delete old file if different
                        if old_url_1 and old_url_1 != preview1:
                            try:
                                drive_delete(old_url_1)
                            except Exception:
                                pass
                    
                    # If we have a second image, use it as back
                    if len(imgs) > 1:
                        f2 = imgs[1]
                        ext2 = _pick_ext((getattr(f2, 'name', '') or '').lower())
                        up2 = drive_upload(f2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2
                            # Delete old back file if different
                            if old_url_2 and old_url_2 != preview2:
                                try:
                                    drive_delete(old_url_2)
                                except Exception:
                                    pass
                    else:
                        # No back image provided - keep existing if modifying, else clear
                        if app and old_url_2:
                            inst.aadhaar_file_url_2 = old_url_2
                        else:
                            inst.aadhaar_file_url_2 = ''
            else:'''
    
    new_imgs = '''                else:
                    # Upload image as front
                    def _pick_ext(nm: str):
                        if nm.endswith('.png'): return '.png'
                        if nm.endswith('.webp'): return '.webp'
                        return '.jpg'
                    
                    ext1 = _pick_ext(name)
                    up1 = drive_upload(aadhaar_file_1, f"aadhaar_{request.user.id}_front{ext1}", folder)
                    if up1:
                        _fid1, preview1 = up1
                        inst.aadhaar_file_url = preview1
                        # Delete old file if different
                        if old_url_1 and old_url_1 != preview1:
                            try:
                                drive_delete(old_url_1)
                            except Exception:
                                pass
                    
                    # Handle optional Document 2 (back side)
                    if aadhaar_file_2:
                        name2 = (getattr(aadhaar_file_2, 'name', '') or '').lower()
                        ext2 = _pick_ext(name2)
                        up2 = drive_upload(aadhaar_file_2, f"aadhaar_{request.user.id}_back{ext2}", folder)
                        if up2:
                            _fid2, preview2 = up2
                            inst.aadhaar_file_url_2 = preview2
                            # Delete old back file if different
                            if old_url_2 and old_url_2 != preview2:
                                try:
                                    drive_delete(old_url_2)
                                except Exception:
                                    pass
                    else:
                        # No back image provided - keep existing if modifying, else clear
                        if app and old_url_2:
                            inst.aadhaar_file_url_2 = old_url_2
                        else:
                            inst.aadhaar_file_url_2 = ''
            else:'''
    
    if old_imgs in content:
        content = content.replace(old_imgs, new_imgs)
        print("✓ Updated application_fill images handling")
    else:
        print("✗ Images pattern not found")
    
    # Write back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✓ All updates complete")

if __name__ == '__main__':
    main()
