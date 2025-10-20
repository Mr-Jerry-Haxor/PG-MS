"""
Asynchronous PDF Generation Task Manager
Handles background PDF generation with progress tracking
"""
import os
import threading
import time
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone


class PDFTaskManager:
    """
    Simple in-memory task manager for PDF generation.
    For production, consider using Celery or Django-Q.
    """
    _tasks = {}  # task_id -> task_info
    _lock = threading.Lock()
    
    @classmethod
    def create_task(cls, user_id, pg_id, pg_name):
        """Create a new PDF generation task"""
        task_id = f"pdf_{user_id}_{pg_id}_{int(time.time() * 1000)}"
        
        with cls._lock:
            cls._tasks[task_id] = {
                'task_id': task_id,
                'user_id': user_id,
                'pg_id': pg_id,
                'pg_name': pg_name,
                'status': 'pending',  # pending, processing, completed, failed
                'progress': 0,  # 0-100
                'message': 'Initializing PDF generation...',
                'file_path': None,
                'created_at': timezone.now(),
                'completed_at': None,
                'error': None,
            }
        
        return task_id
    
    @classmethod
    def get_task(cls, task_id):
        """Get task information"""
        with cls._lock:
            return cls._tasks.get(task_id)
    
    @classmethod
    def update_task(cls, task_id, **kwargs):
        """Update task information"""
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id].update(kwargs)
    
    @classmethod
    def delete_task(cls, task_id):
        """Delete task and associated file"""
        with cls._lock:
            task = cls._tasks.get(task_id)
            if task and task.get('file_path'):
                try:
                    if os.path.exists(task['file_path']):
                        os.remove(task['file_path'])
                except Exception:
                    pass
            
            if task_id in cls._tasks:
                del cls._tasks[task_id]
    
    @classmethod
    def cleanup_old_tasks(cls, hours=24):
        """Clean up tasks older than specified hours"""
        cutoff = timezone.now() - timedelta(hours=hours)
        
        with cls._lock:
            task_ids = list(cls._tasks.keys())
            for task_id in task_ids:
                task = cls._tasks[task_id]
                if task['created_at'] < cutoff:
                    # Delete file if exists
                    if task.get('file_path'):
                        try:
                            if os.path.exists(task['file_path']):
                                os.remove(task['file_path'])
                        except Exception:
                            pass
                    # Remove from memory
                    del cls._tasks[task_id]
    
    @classmethod
    def get_user_active_task(cls, user_id, pg_id):
        """Get user's active task for a specific PG"""
        with cls._lock:
            for task_id, task in cls._tasks.items():
                if (task['user_id'] == user_id and 
                    task['pg_id'] == pg_id and 
                    task['status'] in ['pending', 'processing']):
                    return task_id, task
        return None, None


def get_pdf_storage_dir():
    """Get directory for storing generated PDFs"""
    pdf_dir = os.path.join(settings.MEDIA_ROOT, 'temp_pdfs')
    os.makedirs(pdf_dir, exist_ok=True)
    return pdf_dir
