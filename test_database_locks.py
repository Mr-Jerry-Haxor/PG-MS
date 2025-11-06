"""
Test script to verify database lock handling.

This script simulates concurrent database operations to ensure
the database lock fixes are working properly.
"""
import os
import django
import threading
import time
from decimal import Decimal

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pgms.settings')
django.setup()

from django.db import connection
from bookings.models import Booking
from finance.models import Payment
from django.contrib.auth import get_user_model

User = get_user_model()


def test_concurrent_saves():
    """Test concurrent save operations on the same model."""
    print("\n=== Testing Concurrent Saves ===")
    
    # Get a booking to update
    booking = Booking.objects.filter(status=Booking.APPROVED).first()
    if not booking:
        print("❌ No approved bookings found to test with")
        return False
    
    original_id = booking.id
    errors = []
    success_count = [0]  # Use list to allow modification in thread
    
    def save_operation(thread_num):
        """Simulate a save operation."""
        try:
            # Refresh from database
            b = Booking.objects.get(id=original_id)
            # Simulate some processing time
            time.sleep(0.01)
            # Save back (no actual changes, just testing the save)
            b.save()
            success_count[0] += 1
            print(f"  ✓ Thread {thread_num} completed successfully")
        except Exception as e:
            error_msg = str(e)
            errors.append(f"Thread {thread_num}: {error_msg}")
            print(f"  ❌ Thread {thread_num} failed: {error_msg}")
    
    # Run 10 concurrent save operations
    threads = []
    for i in range(10):
        t = threading.Thread(target=save_operation, args=(i+1,))
        threads.append(t)
        t.start()
    
    # Wait for all threads to complete
    for t in threads:
        t.join()
    
    print(f"\nResults: {success_count[0]}/10 operations succeeded")
    
    if errors:
        print(f"❌ {len(errors)} errors occurred:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("✅ All concurrent operations completed successfully!")
        return True


def test_wal_mode():
    """Verify WAL mode is enabled."""
    print("\n=== Testing WAL Mode ===")
    
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        
        if mode.lower() == 'wal':
            print(f"✅ WAL mode is enabled: {mode}")
            return True
        else:
            print(f"❌ WAL mode is NOT enabled. Current mode: {mode}")
            print("   Run: python manage.py optimize_sqlite")
            return False


def test_busy_timeout():
    """Verify busy timeout is set."""
    print("\n=== Testing Busy Timeout ===")
    
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        
        if timeout >= 30000:
            print(f"✅ Busy timeout is set: {timeout}ms")
            return True
        else:
            print(f"❌ Busy timeout is too low: {timeout}ms (should be 30000ms)")
            return False


def test_db_utils():
    """Test the database utility functions."""
    print("\n=== Testing Database Utilities ===")
    
    try:
        from core.db_utils import sqlite_retry_on_lock, retry_on_db_lock
        
        # Test context manager
        with sqlite_retry_on_lock():
            booking = Booking.objects.first()
            if booking:
                booking.save()
        
        print("✅ Context manager (sqlite_retry_on_lock) works")
        
        # Test decorator
        @retry_on_db_lock()
        def test_operation():
            booking = Booking.objects.first()
            if booking:
                booking.save()
            return True
        
        result = test_operation()
        print("✅ Decorator (retry_on_db_lock) works")
        
        return True
        
    except Exception as e:
        print(f"❌ Database utilities test failed: {e}")
        return False


def test_middleware_import():
    """Test that middleware can be imported."""
    print("\n=== Testing Middleware Import ===")
    
    try:
        from core.middleware import (
            DatabaseRetryMiddleware,
            DatabaseConnectionMiddleware,
            SQLiteOptimizationMiddleware
        )
        print("✅ All middleware classes imported successfully")
        return True
    except Exception as e:
        print(f"❌ Middleware import failed: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("DATABASE LOCK FIX - VERIFICATION TESTS")
    print("=" * 60)
    
    results = {
        'WAL Mode': test_wal_mode(),
        'Busy Timeout': test_busy_timeout(),
        'Middleware Import': test_middleware_import(),
        'Database Utilities': test_db_utils(),
        'Concurrent Operations': test_concurrent_saves(),
    }
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name}: {status}")
    
    all_passed = all(results.values())
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
        print("\nYour database is properly configured to handle concurrent")
        print("operations without lock errors.")
    else:
        print("⚠️  SOME TESTS FAILED")
        print("\nPlease review the errors above and:")
        print("1. Run: python manage.py optimize_sqlite")
        print("2. Restart your Django server")
        print("3. Run this test again")
    print("=" * 60)
    
    return all_passed


if __name__ == '__main__':
    main()
