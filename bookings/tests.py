from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import datetime, timedelta
from unittest.mock import patch

from pgadmin.models import PG, PGAdmin

from .models import Booking, ResidentApplication, Room, RoomShareStatus
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


class DayWisePendingAssignmentTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='daywise-user', email='daywise@example.com', password='test-password'
        )
        self.admin = User.objects.create_user(
            username='daywise-admin', email='daywise-admin@example.com', password='test-password'
        )
        self.pg = PG.objects.create(name='Day-wise Test PG', address='Test address')
        PGAdmin.objects.create(user=self.admin, pg=self.pg)

    def daywise_payload(self, start=None, end=None, start_time='10:00', end_time='12:00'):
        start = start or (timezone.localdate() + timedelta(days=1))
        end = end or start
        return {
            'booking_type': 'daywise',
            'daywise_name': 'Short Stay Guest',
            'daywise_mobile': '9876543210',
            'daywise_emergency': '9123456780',
            'daywise_start_date': start.isoformat(),
            'daywise_end_date': end.isoformat(),
            'daywise_start_time': start_time,
            'daywise_end_time': end_time,
            'daywise_purpose': 'Short business visit',
            'daywise_selfie_data': 'data:image/png;base64,aA==',
            'daywise_aadhaar1': SimpleUploadedFile('aadhaar.pdf', b'pdf', content_type='application/pdf'),
        }

    @patch('core.drive.drive_upload', return_value=('file-id', 'https://example.com/file'))
    def test_submission_creates_roomless_pending_booking_and_application(self, _upload):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('pg_quick_booking', args=[self.pg.slug]),
            data=self.daywise_payload(),
        )
        self.assertRedirects(response, reverse('dashboard'))
        booking = Booking.objects.get(user=self.user, booking_type=Booking.DAYWISE)
        self.assertEqual(booking.status, Booking.PENDING)
        self.assertIsNone(booking.room_id)
        self.assertIsNone(booking.share_no)
        self.assertEqual(booking.pg, self.pg)
        self.assertIsNone(booking.application.room_id)

    def test_pending_page_includes_unassigned_daywise_booking(self):
        booking = Booking.objects.create(
            user=self.user, pg=self.pg, room=None, share_no=None,
            booking_type=Booking.DAYWISE, status=Booking.PENDING,
            joining_date=timezone.localdate() + timedelta(days=1),
            leaving_date=timezone.localdate() + timedelta(days=2),
        )
        ResidentApplication.objects.create(
            user=self.user, booking=booking, pg=self.pg, room=None,
            name='Visible Pending Guest', email=self.user.email,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse('pg_bookings_pending'), {'pg': self.pg.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visible Pending Guest')
        self.assertContains(response, 'Room Not Assigned')

    def test_approval_assigns_application_and_reserves_future_bed(self):
        room = Room.objects.create(pg=self.pg, room_no='101', total_shares=1)
        share = RoomShareStatus.objects.create(room=room, share_no=1, status=RoomShareStatus.VACANT)
        booking = Booking.objects.create(
            user=self.user, pg=self.pg, room=None, share_no=None,
            booking_type=Booking.DAYWISE, status=Booking.PENDING,
            joining_date=timezone.localdate() + timedelta(days=1),
            leaving_date=timezone.localdate() + timedelta(days=2),
            start_time=timezone.datetime.strptime('10:00', '%H:%M').time(),
            end_time=timezone.datetime.strptime('12:00', '%H:%M').time(),
        )
        application = ResidentApplication.objects.create(
            user=self.user, booking=booking, pg=self.pg, room=None,
            name='Assigned Guest', email=self.user.email,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse('pg_booking_approve', args=[booking.id]), {
            'room_id': room.id, 'share_no': '1', 'payment_amount': '0',
        })
        self.assertRedirects(response, reverse('pg_bookings_pending'))
        booking.refresh_from_db()
        application.refresh_from_db()
        share.refresh_from_db()
        self.assertEqual(booking.status, Booking.APPROVED)
        self.assertEqual((booking.room_id, booking.share_no), (room.id, 1))
        self.assertEqual(application.room_id, room.id)
        self.assertEqual(application.status, ResidentApplication.CONFIRMED)
        self.assertEqual(share.status, RoomShareStatus.RESERVED)

    def test_database_rejects_approved_unassigned_daywise_booking(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Booking.objects.create(
                    user=self.user, pg=self.pg, room=None, share_no=None,
                    booking_type=Booking.DAYWISE, status=Booking.APPROVED,
                )

    def test_approval_rechecks_overlapping_booking(self):
        other_user = get_user_model().objects.create_user(username='other-daywise')
        room = Room.objects.create(pg=self.pg, room_no='202', total_shares=1)
        RoomShareStatus.objects.create(room=room, share_no=1, status=RoomShareStatus.VACANT)
        start = timezone.localdate() + timedelta(days=2)
        existing = Booking.objects.create(
            user=other_user, pg=self.pg, room=room, share_no=1,
            booking_type=Booking.DAYWISE, status=Booking.APPROVED,
            joining_date=start, leaving_date=start + timedelta(days=1),
            start_time=timezone.datetime.strptime('09:00', '%H:%M').time(),
            end_time=timezone.datetime.strptime('11:00', '%H:%M').time(),
        )
        pending = Booking.objects.create(
            user=self.user, pg=self.pg, room=None, share_no=None,
            booking_type=Booking.DAYWISE, status=Booking.PENDING,
            joining_date=start, leaving_date=start,
            start_time=timezone.datetime.strptime('10:00', '%H:%M').time(),
            end_time=timezone.datetime.strptime('12:00', '%H:%M').time(),
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse('pg_booking_approve', args=[pending.id]), {
            'room_id': room.id, 'share_no': '1', 'payment_amount': '0',
        }, follow=True)
        self.assertContains(response, 'another booking during this stay')
        pending.refresh_from_db()
        self.assertEqual(pending.status, Booking.PENDING)
        self.assertIsNone(pending.room_id)
        self.assertEqual(existing.status, Booking.APPROVED)

    @patch('core.drive.drive_upload', return_value=('file-id', 'https://example.com/file'))
    def test_daywise_request_can_coexist_with_active_regular_booking(self, _upload):
        room = Room.objects.create(pg=self.pg, room_no='301', total_shares=1)
        RoomShareStatus.objects.create(room=room, share_no=1, status=RoomShareStatus.OCCUPIED)
        Booking.objects.create(
            user=self.user, pg=self.pg, room=room, share_no=1,
            booking_type=Booking.REGULAR, status=Booking.APPROVED,
            joining_date=timezone.localdate(),
        )
        self.client.force_login(self.user)
        page = self.client.get(reverse('pg_quick_booking', args=[self.pg.slug]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Existing regular booking', count=2)
        self.assertContains(page, 'Day-wise Booking')
        response = self.client.post(
            reverse('pg_quick_booking', args=[self.pg.slug]), data=self.daywise_payload()
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(Booking.objects.filter(user=self.user, booking_type=Booking.DAYWISE).count(), 1)

    @patch('core.drive.drive_upload', return_value=('file-id', 'https://example.com/file'))
    def test_same_day_checkout_must_be_after_checkin(self, _upload):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('pg_quick_booking', args=[self.pg.slug]),
            data=self.daywise_payload(start_time='12:00', end_time='10:00'),
            follow=True,
        )
        self.assertContains(response, 'Check-out must be after check-in.')
        self.assertFalse(Booking.objects.filter(user=self.user, booking_type=Booking.DAYWISE).exists())

    def test_daywise_bed_transitions_at_checkin_and_checkout_times(self):
        room = Room.objects.create(pg=self.pg, room_no='401', total_shares=1)
        share = RoomShareStatus.objects.create(room=room, share_no=1, status=RoomShareStatus.RESERVED)
        stay_date = timezone.datetime(2030, 1, 15).date()
        booking = Booking.objects.create(
            user=self.user, pg=self.pg, room=room, share_no=1,
            booking_type=Booking.DAYWISE, status=Booking.APPROVED,
            joining_date=stay_date, leaving_date=stay_date,
            start_time=timezone.datetime.strptime('11:00', '%H:%M').time(),
            end_time=timezone.datetime.strptime('13:00', '%H:%M').time(),
        )

        def sync_at(hour):
            moment = timezone.make_aware(datetime(2030, 1, 15, hour, 0))
            with patch('bookings.utils.timezone.localtime', return_value=moment), patch(
                'bookings.utils.timezone.localdate', return_value=stay_date
            ):
                sync_room_share_statuses(self.pg)
            share.refresh_from_db()
            booking.refresh_from_db()

        sync_at(10)
        self.assertEqual((booking.status, share.status), (Booking.APPROVED, RoomShareStatus.RESERVED))
        sync_at(12)
        self.assertEqual((booking.status, share.status), (Booking.APPROVED, RoomShareStatus.OCCUPIED))
        sync_at(14)
        self.assertEqual((booking.status, share.status), (Booking.COMPLETED, RoomShareStatus.VACANT))
