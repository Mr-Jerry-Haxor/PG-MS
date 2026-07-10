from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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


class PendingApplicationEditingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='applicant',
            email='applicant@example.com',
            password='test-password',
        )
        self.pg = PG.objects.create(name='Application Test PG', address='Test address')
        self.room = Room.objects.create(pg=self.pg, room_no='201', total_shares=1)
        RoomShareStatus.objects.create(room=self.room, share_no=1)
        self.client.force_login(self.user)

    def test_pending_booking_application_form_is_editable(self):
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            share_no=1,
            status=Booking.PENDING,
        )

        response = self.client.get(reverse('application_fill', args=[booking.id]))

        self.assertEqual(response.status_code, 200)

    def test_approved_booking_application_form_is_not_editable(self):
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            share_no=1,
            status=Booking.APPROVED,
        )

        response = self.client.get(reverse('application_fill', args=[booking.id]))

        self.assertRedirects(response, reverse('dashboard'))

    def test_quick_booking_does_not_prefill_personal_details(self):
        response = self.client.get(reverse('pg_quick_booking', args=[self.pg.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial.get('name', ''), '')
        self.assertEqual(response.context['form'].initial.get('phone', ''), '')
        self.assertNotContains(response, 'value="Applicant"')
        self.assertNotContains(response, 'placeholder=')
