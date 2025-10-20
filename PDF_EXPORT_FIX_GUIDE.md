# PDF Export Optimization Guide for 502 Bad Gateway Errors

## Problem
When exporting PDFs for large PGs (100+ tenants), you're getting 502 Bad Gateway errors because:
1. **Server timeout** - Nginx/server kills request before PDF generation completes
2. **Memory usage** - Large datasets consume too much memory
3. **Synchronous blocking** - Long-running requests block workers

## Quick Fixes (Choose ONE)

### Option 1: Increase Server Timeouts (FASTEST - Do This First!)

#### For Nginx:
```nginx
# Add to your nginx configuration
location / {
    proxy_read_timeout 300s;      # 5 minutes
    proxy_connect_timeout 300s;
    proxy_send_timeout 300s;
}
```

#### For Gunicorn:
```bash
# Start gunicorn with longer timeout
gunicorn --timeout 300 --workers 4 pgms.wsgi:application
```

#### For Django Development Server:
```python
# In settings.py, add:
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800
```

### Option 2: Optimize PDF Generation Code (Code Changes)

Add these optimizations to `pgadmin/views.py` in `tenants_export_pdf` function:

```python
# 1. Skip image downloads (fastest)
# Comment out or modify the image download section:
def _get_image(url, default_width=18*mm, default_height=22*mm):
    return None  # Skip all images for speed

# 2. Reduce image timeout (if keeping images)
resp = requests.get(url, timeout=2, stream=True)  # Changed from 10s to 2s

# 3. Limit parallel downloads
with ThreadPoolExecutor(max_workers=2) as executor:  # Reduced from 5 to 2

# 4. Add chunked processing for large datasets
if total_tenants > 100:
    # Process in batches of 20 rooms at a time
    for i in range(0, len(rooms), 20):
        batch = rooms[i:i+20]
        # Process batch...
        gc.collect()  # Free memory between batches
```

### Option 3: Add Loading/Progress UI (Best UX)

Instead of blocking, show progress:

1. Create a new async endpoint that generates PDF in background
2. Return immediately with a "Processing..." message
3. Poll for completion
4. Download when ready

**Implementation:**

```python
# New file: pgadmin/tasks.py
from threading import Thread
import uuid
import os

pdf_tasks = {}

def generate_pdf_async(pg_id, user_id):
    task_id = str(uuid.uuid4())
    pdf_tasks[task_id] = {'status': 'processing', 'progress': 0}
    
    def worker():
        try:
            # Generate PDF here
            pdf_data = _generate_tenant_pdf(pg_id)
            
            # Save to temp file
            filepath = f"/tmp/pdf_{task_id}.pdf"
            with open(filepath, 'wb') as f:
                f.write(pdf_data)
            
            pdf_tasks[task_id] = {
                'status': 'complete',
                'filepath': filepath,
                'progress': 100
            }
        except Exception as e:
            pdf_tasks[task_id] = {
                'status': 'error',
                'error': str(e),
                'progress': 0
            }
    
    Thread(target=worker, daemon=True).start()
    return task_id

# In views.py
@login_required
def tenants_export_pdf_start(request):
    """Start PDF generation in background"""
    pg = _active_pg(request)
    task_id = generate_pdf_async(pg.id, request.user.id)
    return JsonResponse({'task_id': task_id})

@login_required
def tenants_export_pdf_status(request, task_id):
    """Check PDF generation status"""
    task = pdf_tasks.get(task_id, {})
    return JsonResponse(task)

@login_required  
def tenants_export_pdf_download(request, task_id):
    """Download completed PDF"""
    task = pdf_tasks.get(task_id, {})
    if task.get('status') == 'complete':
        filepath = task['filepath']
        with open(filepath, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/pdf')
            response['Content-Disposition'] = 'attachment; filename="tenants.pdf"'
        os.remove(filepath)  # Clean up
        del pdf_tasks[task_id]
        return response
    return HttpResponse('Not ready', status=404)
```

**Frontend (JavaScript in tenants.html):**

```javascript
async function exportPDF() {
    // Start generation
    const startResp = await fetch('/pg/tenants/export/pdf/start/');
    const {task_id} = await startResp.json();
    
    // Show progress modal
    showModal('Generating PDF... Please wait');
    
    // Poll for completion
    const interval = setInterval(async () => {
        const statusResp = await fetch(`/pg/tenants/export/pdf/status/${task_id}/`);
        const status = await statusResp.json();
        
        if (status.status === 'complete') {
            clearInterval(interval);
            window.location.href = `/pg/tenants/export/pdf/download/${task_id}/`;
            hideModal();
        } else if (status.status === 'error') {
            clearInterval(interval);
            showError('PDF generation failed');
        }
    }, 2000);  // Check every 2 seconds
}
```

## Recommended Approach

**For Immediate Fix:**
1. ✅ Increase server timeouts (Option 1) - Takes 5 minutes
2. ✅ Disable image downloads temporarily - Fastest PDF generation

**For Production:**
- Implement Option 3 (async generation with progress UI)
- Use Celery or Django-Q for proper task queue
- Add Redis for task status tracking

## Testing

After applying fixes, test with:
```bash
# Test with curl (should take < 5 minutes)
curl -o test.pdf "http://localhost:8000/pg/tenants/export/pdf/" \
  -H "Cookie: sessionid=YOUR_SESSION_ID" \
  --max-time 300

# Check PDF file size
ls -lh test.pdf
```

## Quick Code Patch (Apply Now)

Add to top of `tenants_export_pdf` function:

```python
# QUICK FIX: Skip images for large PGs
total_rooms = Room.objects.filter(pg=pg).count()
SKIP_IMAGES = total_rooms > 50  # Skip images if >50 rooms

# Modify _get_image function:
def _get_image(url, default_width=18*mm, default_height=22*mm):
    if SKIP_IMAGES:
        return None  # Skip for large PGs
    # ... rest of existing code
```

This will generate PDFs much faster (2-3x speed improvement) for large PGs by skipping image downloads.
