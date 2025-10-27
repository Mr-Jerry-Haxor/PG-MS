from django.core.management.base import BaseCommand
from django.utils import timezone
from bookings.models import Booking, RoomShareStatus
from django.db import transaction

class Command(BaseCommand):
    help = "Auto-activate bookings whose joining date has arrived (change RESERVED to OCCUPIED)"

    def handle(self, *args, **options):
        today = timezone.now().date()
        
        # Find approved bookings with joining_date <= today but share still RESERVED
        qs = (
            Booking.objects.filter(
                status=Booking.APPROVED,
                joining_date__isnull=False,
                joining_date__lte=today
            )
            .select_related('room')
            .order_by('room_id', 'share_no')
        )
        
        activated = 0
        for bk in qs:
            share = bk.room.shares.filter(share_no=bk.share_no).first()
            if not share:
                continue
            
            # Only update if share is currently RESERVED
            with transaction.atomic():
                if share.status == RoomShareStatus.RESERVED:
                    share.status = RoomShareStatus.OCCUPIED
                    share.save(update_fields=['status'])
                    activated += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Activated: Room {bk.room.room_no} Bed {bk.share_no} for {bk.user.email}"
                        )
                    )
        
        self.stdout.write(
            self.style.SUCCESS(f"Auto-activate completed. Shares activated: {activated}")
        )
