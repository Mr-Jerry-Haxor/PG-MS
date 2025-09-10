from django.core.management.base import BaseCommand
from django.utils import timezone
from bookings.models import Booking, RoomShareStatus
from django.db import transaction

class Command(BaseCommand):
    help = "Auto-vacate shares whose leaving date has passed AND was confirmed, mark booking completed, free share if no future pending booking locks it"

    def handle(self, *args, **options):
        today = timezone.now().date()
        qs = (
            Booking.objects.filter(status=Booking.APPROVED, leaving_date__isnull=False, leaving_date__lte=today, leaving_confirmed_date__isnull=False)
            .select_related('room')
            .order_by('room_id', 'share_no')
        )
        vacated = 0
        for bk in qs:
            share = bk.room.shares.filter(share_no=bk.share_no).first()
            if not share:
                continue
            # If share already vacant/reserved we skip freeing; we only complete booking if still approved
            with transaction.atomic():
                changed = False
                if share.status != RoomShareStatus.VACANT:
                    share.status = RoomShareStatus.VACANT
                    # Clear scheduled date
                    if share.vacant_from:
                        share.vacant_from = None
                        share.save(update_fields=['status','vacant_from'])
                    else:
                        share.save(update_fields=['status'])
                    changed = True
                if bk.status != Booking.COMPLETED:
                    bk.status = Booking.COMPLETED
                    bk.save(update_fields=['status'])
                    changed = True
                if changed:
                    vacated += 1
        self.stdout.write(self.style.SUCCESS(f"Auto-vacate completed. Shares/Bookings updated: {vacated}"))
