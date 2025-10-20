# Image Cache URL Mismatch Fix

## Problem Identified

Images were being successfully downloaded and cached but not appearing in the final PDF because of a **URL mismatch** between the storage phase and retrieval phase.

### Root Cause

**During Image Download (Storage Phase):**
```python
def _get_image(url, ...):
    # URLs are normalized before caching
    if 'drive.google.com/file/d/' in url:
        # Convert: drive.google.com/file/d/ID/view
        # To: drive.google.com/uc?export=download&id=ID
        url = f'https://drive.google.com/uc?export=download&id={file_id}'
    elif 'dropbox.com' in url and '?dl=0' in url:
        # Convert: ?dl=0 → ?dl=1
        url = url.replace('?dl=0', '?dl=1')
    
    # Store with normalized URL
    _image_cache[normalized_url] = rl_img
```

**During Card Generation (Retrieval Phase - BEFORE FIX):**
```python
# Raw URL from database (NOT normalized)
selfie_url = getattr(app, 'selfie_url', None)

# Cache lookup fails because URLs don't match
selfie_img = _image_cache.get(selfie_url)  # ❌ Returns None
```

### Example Mismatch

**Storage:**
- Key: `https://drive.google.com/uc?export=download&id=ABC123`
- Value: ReportLab Image object

**Retrieval (Before Fix):**
- Looking for: `https://drive.google.com/file/d/ABC123/view?usp=sharing`
- Result: **Cache miss** → `None` → "No Photo" displayed

## Solution Implemented

Added URL normalization in the card generation phase to match the download phase normalization:

```python
# pgadmin/views.py - Lines ~2869-2890

# Get raw URL from database
selfie_url = getattr(app, 'selfie_url', None) or getattr(getattr(user, 'profile', None), 'selfie_url', None)

# Normalize URL before cache lookup (must match normalization during download)
normalized_url = None
if selfie_url:
    normalized_url = selfie_url
    # Google Drive: file/d/ID/view → uc?export=download&id=ID
    if 'drive.google.com/file/d/' in normalized_url:
        parts = normalized_url.split('/d/')
        if len(parts) > 1:
            file_id = parts[1].split('/')[0]
            normalized_url = f'https://drive.google.com/uc?export=download&id={file_id}'
    # Dropbox: ?dl=0 → ?dl=1
    elif 'dropbox.com' in normalized_url and '?dl=0' in normalized_url:
        normalized_url = normalized_url.replace('?dl=0', '?dl=1')

# Now cache lookup succeeds
selfie_img = _image_cache.get(normalized_url) if normalized_url else None
```

## How It Works Now

### Phase 1: Image Download
1. Background thread starts
2. Collects all image URLs from database
3. For each URL:
   - Normalize URL (Drive/Dropbox conversion)
   - Download image
   - Process with PIL (resize, RGB conversion)
   - Create ReportLab Image object
   - **Store in cache: `_image_cache[normalized_url] = image`**

### Phase 2: Card Generation
1. For each booking:
   - Get raw `selfie_url` from database
   - **Normalize URL using same logic as download phase**
   - Lookup in cache: `_image_cache.get(normalized_url)`
   - **Cache hit!** → Image retrieved successfully
   - Add image to PDF card

### Phase 3: PDF Creation
- Cards now contain actual images instead of "No Photo" placeholders
- 3-cards-per-row layout maintained
- All images render correctly in final PDF

## Testing Verification

After this fix:
1. Click "Export PDF" button
2. Progress modal shows image loading: "Loading images: 50/50 ✓"
3. Click download when complete
4. Open PDF file
5. **Expected Result**: All tenant selfie photos visible in cards
6. **Previous Result**: "No Photo" placeholders everywhere

## Key Learnings

### URL Normalization Must Be Consistent

When using in-memory caching with URLs as keys:
- **Always normalize URLs before storage**
- **Always normalize URLs before retrieval**
- **Use identical normalization logic in both places**

### Google Drive URL Formats

Raw URL from database:
```
https://drive.google.com/file/d/1ABC123XYZ/view?usp=sharing
```

Normalized for direct download:
```
https://drive.google.com/uc?export=download&id=1ABC123XYZ
```

### Dropbox URL Formats

Raw URL from database:
```
https://www.dropbox.com/s/abc123/photo.jpg?dl=0
```

Normalized for direct download:
```
https://www.dropbox.com/s/abc123/photo.jpg?dl=1
```

## Code Location

**File**: `pgadmin/views.py`
**Function**: `_generate_pdf_async()`
**Lines**: ~2869-2890 (card generation section)

## Related Issues Fixed

1. ✅ RoomShareStatus attribute error (booking_id)
2. ✅ Missing datetime import
3. ✅ Layout broken (cards stacked vertically)
4. ✅ Parallel image loading not working
5. ✅ **URL mismatch preventing cached images from displaying**

## Performance Impact

**No performance penalty** - URL normalization is just string manipulation:
- Split/join operations: O(1)
- String replacement: O(n) where n = URL length (~100 chars)
- Total overhead: < 1ms per URL
- Cache lookup: Still O(1) hash table lookup

## Summary

The fix ensures that URLs are normalized consistently across both the image download phase and the card generation phase. This allows the in-memory cache to successfully retrieve images using the same keys that were used during storage, resolving the issue where images appeared to download successfully but didn't show up in the final PDF.

**Status**: ✅ FIXED - Images now display correctly in exported PDFs
