"""
Management command to optimize SQLite database configuration.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Optimize SQLite database for better concurrency and performance'

    def handle(self, *args, **options):
        if connection.vendor != 'sqlite':
            self.stdout.write(
                self.style.WARNING(
                    'This command is only applicable to SQLite databases.'
                )
            )
            return
        
        self.stdout.write('Optimizing SQLite database...')
        
        with connection.cursor() as cursor:
            # Check current journal mode
            cursor.execute("PRAGMA journal_mode")
            current_mode = cursor.fetchone()[0]
            self.stdout.write(f'  Current journal mode: {current_mode}')
            
            # Enable Write-Ahead Logging (WAL) for better concurrency
            cursor.execute("PRAGMA journal_mode=WAL")
            new_mode = cursor.fetchone()[0]
            self.stdout.write(
                self.style.SUCCESS(f'  ✓ Journal mode set to: {new_mode}')
            )
            
            # Set synchronous to NORMAL for better performance
            cursor.execute("PRAGMA synchronous=NORMAL")
            self.stdout.write(
                self.style.SUCCESS('  ✓ Synchronous mode set to NORMAL')
            )
            
            # Set busy timeout to 30 seconds
            cursor.execute("PRAGMA busy_timeout=30000")
            self.stdout.write(
                self.style.SUCCESS('  ✓ Busy timeout set to 30 seconds')
            )
            
            # Increase cache size to 64MB
            cursor.execute("PRAGMA cache_size=-64000")
            self.stdout.write(
                self.style.SUCCESS('  ✓ Cache size set to 64MB')
            )
            
            # Store temp tables in memory
            cursor.execute("PRAGMA temp_store=MEMORY")
            self.stdout.write(
                self.style.SUCCESS('  ✓ Temp storage set to MEMORY')
            )
            
            # Analyze the database for query optimization
            cursor.execute("ANALYZE")
            self.stdout.write(
                self.style.SUCCESS('  ✓ Database analyzed for optimization')
            )
            
            # Vacuum the database to reclaim space and defragment
            self.stdout.write('  Running VACUUM (this may take a moment)...')
            cursor.execute("VACUUM")
            self.stdout.write(
                self.style.SUCCESS('  ✓ Database vacuumed and defragmented')
            )
        
        self.stdout.write(
            self.style.SUCCESS(
                '\n✓ SQLite database optimization complete!\n'
                '\nRecommendations:\n'
                '  1. Run this command after major data changes\n'
                '  2. WAL mode creates -wal and -shm files (this is normal)\n'
                '  3. These optimizations persist across server restarts\n'
                '  4. For production, consider migrating to PostgreSQL for better concurrency'
            )
        )
