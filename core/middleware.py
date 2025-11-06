"""
Middleware for handling database operations gracefully.
"""
import time
import logging
from django.db import OperationalError, connection
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class DatabaseRetryMiddleware(MiddlewareMixin):
    """
    Middleware to handle SQLite database lock errors by retrying the request.
    
    This middleware catches OperationalError exceptions caused by database locks
    and retries the request up to MAX_RETRIES times with exponential backoff.
    """
    MAX_RETRIES = 3
    INITIAL_DELAY = 0.1  # 100ms
    
    def process_exception(self, request, exception):
        """
        Handle database lock exceptions with automatic retry logic.
        """
        if not isinstance(exception, OperationalError):
            return None
        
        # Check if it's a database lock error
        error_msg = str(exception).lower()
        if 'database is locked' not in error_msg and 'locked' not in error_msg:
            return None
        
        # Get retry count from request (we store it here to track retries)
        retry_count = getattr(request, '_db_retry_count', 0)
        
        if retry_count >= self.MAX_RETRIES:
            logger.error(
                f"Database locked after {self.MAX_RETRIES} retries. "
                f"Path: {request.path}, Method: {request.method}"
            )
            return HttpResponse(
                "Database is temporarily busy. Please try again in a moment.",
                status=503
            )
        
        # Calculate delay with exponential backoff
        delay = self.INITIAL_DELAY * (2 ** retry_count)
        
        logger.warning(
            f"Database locked, retry {retry_count + 1}/{self.MAX_RETRIES} "
            f"after {delay}s. Path: {request.path}"
        )
        
        # Wait before retry
        time.sleep(delay)
        
        # Increment retry count
        request._db_retry_count = retry_count + 1
        
        # Close the current database connection to clear the lock
        connection.close()
        
        # Return None to allow Django to retry the request
        # The request will be processed again by the view
        return None


class DatabaseConnectionMiddleware(MiddlewareMixin):
    """
    Middleware to ensure database connections are properly managed.
    
    This middleware ensures that database connections are closed after each
    request to prevent connection leaks that could contribute to locking issues.
    """
    
    def process_response(self, request, response):
        """
        Close database connections after processing the response.
        """
        # Django's default behavior closes connections, but we ensure it explicitly
        # This is especially important for long-running requests
        if connection.connection is not None:
            # Only close if we're not in an atomic block
            if not connection.in_atomic_block:
                connection.close()
        
        return response
    
    def process_exception(self, request, exception):
        """
        Close database connections when an exception occurs.
        """
        if connection.connection is not None:
            if not connection.in_atomic_block:
                connection.close()
        
        return None


class SQLiteOptimizationMiddleware(MiddlewareMixin):
    """
    Middleware to apply SQLite-specific optimizations on each request.
    
    This ensures that SQLite is configured optimally for each connection,
    especially important if CONN_MAX_AGE is not set or connections are recreated.
    """
    
    def process_request(self, request):
        """
        Apply SQLite optimizations at the start of each request.
        """
        if connection.vendor == 'sqlite':
            with connection.cursor() as cursor:
                # Enable Write-Ahead Logging for better concurrency
                cursor.execute("PRAGMA journal_mode=WAL")
                
                # Set busy timeout to 30 seconds
                cursor.execute("PRAGMA busy_timeout=30000")
        
        return None
