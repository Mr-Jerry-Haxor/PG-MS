"""
Database utility functions for handling SQLite-specific optimizations.
"""
import functools
import logging
import time
from contextlib import contextmanager
from django.db import connection, OperationalError, transaction

logger = logging.getLogger(__name__)


@contextmanager
def sqlite_retry_on_lock(max_retries=3, initial_delay=0.1):
    """
    Context manager that retries database operations on lock errors.
    
    Usage:
        with sqlite_retry_on_lock():
            # Your database operations here
            obj.save()
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds (will use exponential backoff)
    """
    retries = 0
    while True:
        try:
            yield
            break  # Success, exit the loop
        except OperationalError as e:
            error_msg = str(e).lower()
            if 'database is locked' not in error_msg and 'locked' not in error_msg:
                raise  # Re-raise if it's not a lock error
            
            retries += 1
            if retries >= max_retries:
                logger.error(f"Database locked after {max_retries} retries")
                raise
            
            delay = initial_delay * (2 ** (retries - 1))
            logger.warning(f"Database locked, retry {retries}/{max_retries} after {delay}s")
            time.sleep(delay)
            
            # Close and reopen connection
            connection.close()


def retry_on_db_lock(max_retries=3, initial_delay=0.1):
    """
    Decorator that retries a function on database lock errors.
    
    Usage:
        @retry_on_db_lock()
        def my_database_operation():
            # Your database operations here
            obj.save()
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds (will use exponential backoff)
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    error_msg = str(e).lower()
                    if 'database is locked' not in error_msg and 'locked' not in error_msg:
                        raise  # Re-raise if it's not a lock error
                    
                    retries += 1
                    if retries >= max_retries:
                        logger.error(
                            f"Database locked after {max_retries} retries in {func.__name__}"
                        )
                        raise
                    
                    delay = initial_delay * (2 ** (retries - 1))
                    logger.warning(
                        f"Database locked in {func.__name__}, "
                        f"retry {retries}/{max_retries} after {delay}s"
                    )
                    time.sleep(delay)
                    
                    # Close and reopen connection
                    connection.close()
        
        return wrapper
    return decorator


def optimize_sqlite_connection():
    """
    Manually optimize the current SQLite connection.
    
    Call this function when you need to ensure SQLite is optimally configured,
    especially before performing heavy database operations.
    """
    if connection.vendor != 'sqlite':
        return
    
    with connection.cursor() as cursor:
        # Enable Write-Ahead Logging for better concurrency
        cursor.execute("PRAGMA journal_mode=WAL")
        
        # Set busy timeout to 30 seconds
        cursor.execute("PRAGMA busy_timeout=30000")
        
        # Set synchronous to NORMAL for better performance
        cursor.execute("PRAGMA synchronous=NORMAL")
        
        # Increase cache size to 64MB
        cursor.execute("PRAGMA cache_size=-64000")
        
        # Store temp tables in memory
        cursor.execute("PRAGMA temp_store=MEMORY")
        
        logger.debug("SQLite connection optimized")


def execute_with_retry(sql, params=None, max_retries=3):
    """
    Execute raw SQL with automatic retry on database lock.
    
    Args:
        sql: SQL query string
        params: Query parameters (optional)
        max_retries: Maximum number of retry attempts
    
    Returns:
        Cursor object after executing the query
    """
    retries = 0
    while True:
        try:
            with connection.cursor() as cursor:
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                return cursor
        except OperationalError as e:
            error_msg = str(e).lower()
            if 'database is locked' not in error_msg and 'locked' not in error_msg:
                raise
            
            retries += 1
            if retries >= max_retries:
                logger.error(f"Database locked after {max_retries} retries executing SQL")
                raise
            
            delay = 0.1 * (2 ** (retries - 1))
            logger.warning(f"Database locked executing SQL, retry {retries}/{max_retries}")
            time.sleep(delay)
            connection.close()


@contextmanager
def atomic_with_retry(max_retries=3):
    """
    Context manager combining transaction.atomic with retry logic.
    
    This is useful for critical operations that must be atomic but may
    encounter database locks in high-concurrency scenarios.
    
    Usage:
        with atomic_with_retry():
            # Your atomic database operations here
            obj1.save()
            obj2.save()
    """
    retries = 0
    while True:
        try:
            with transaction.atomic():
                yield
            break  # Success
        except OperationalError as e:
            error_msg = str(e).lower()
            if 'database is locked' not in error_msg and 'locked' not in error_msg:
                raise
            
            retries += 1
            if retries >= max_retries:
                logger.error(f"Database locked after {max_retries} retries in atomic block")
                raise
            
            delay = 0.1 * (2 ** (retries - 1))
            logger.warning(
                f"Database locked in atomic block, retry {retries}/{max_retries}"
            )
            time.sleep(delay)
            connection.close()
