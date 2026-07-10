from django.contrib.auth import get_user_model
from django.test import TestCase

from pgadmin.models import PG

from .models import Booking, Room, RoomShareStatus
from .utils import sync_room_share_statuses


class RoomStatusSyncTests(TestCase):
    def test_pending_booking_keeps_bed_reserved(self):
        user = get_user_model().objects.create_user(username='pending-user')
        pg = PG.objects.create(name='Test PG', address='Test address')
        room = Room.objects.create(pg=pg, room_no='101', total_shares=1)
        share = RoomShareStatus.objects.create(
            room=room,
            share_no=1,
            status=RoomShareStatus.VACANT,
        )
        Booking.objects.create(
            user=user,
            room=room,
            share_no=1,
            status=Booking.PENDING,
        )

        sync_room_share_statuses(pg)

        share.refresh_from_db()
        self.assertEqual(share.status, RoomShareStatus.RESERVED)
