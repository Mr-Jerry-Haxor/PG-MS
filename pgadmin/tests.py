import hashlib
import hmac
import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, ResidentApplication, Room, RoomShareStatus
from core.models import Notification
from .models import PG, PGAdmin, WhatsAppCloudConfig, WhatsAppConversation, WhatsAppMessage
from .whatsapp_cloud import WhatsAppCloudError, process_webhook_payload, send_cloud_message
from .whatsapp_crypto import encrypt_secret


class WhatsAppCloudCoexistenceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username='pgadmin', email='admin@example.com', password='x')
        self.pg1 = PG.objects.create(name='PG 1', address='One')
        self.pg2 = PG.objects.create(name='PG 2', address='Two')
        PGAdmin.objects.create(user=self.admin, pg=self.pg1)

    def config(self, pg, phone_id, **sections):
        return WhatsAppCloudConfig.objects.create(
            pg=pg,
            enabled=True,
            phone_number_id=phone_id,
            access_token_encrypted=encrypt_secret('access'),
            verify_token_encrypted=encrypt_secret('verify'),
            app_secret_encrypted=encrypt_secret('secret'),
            **sections,
        )

    @patch('pgadmin.whatsapp_cloud.requests.post')
    def test_default_mode_never_calls_cloud_api(self, post):
        WhatsAppCloudConfig.objects.create(pg=self.pg1)
        with self.assertRaises(WhatsAppCloudError):
            send_cloud_message(pg=self.pg1, to='919999999999', text='hello', section='monthly_dashboard')
        post.assert_not_called()
        self.assertFalse(WhatsAppMessage.objects.exists())

    @patch('pgadmin.whatsapp_cloud.requests.post')
    def test_section_enabled_send_is_persisted(self, post):
        self.config(self.pg1, 'phone-1', enable_monthly_dashboard=True)
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'messages': [{'id': 'wamid.sent'}]}
        post.return_value = response
        sent = send_cloud_message(
            pg=self.pg1, to='+91 99999 99999', text='Rent reminder',
            section='monthly_dashboard', sent_by=self.admin,
        )
        self.assertEqual(sent.status, 'sent')
        self.assertEqual(sent.provider_message_id, 'wamid.sent')
        self.assertEqual(sent.pg, self.pg1)
        self.assertEqual(sent.conversation.contact.wa_id, '919999999999')
        self.assertEqual(post.call_args.kwargs['json']['text']['body'], 'Rent reminder')

    def test_signed_webhook_is_deduplicated_and_tenant_isolated(self):
        self.config(self.pg1, 'phone-1', enable_whatsapp_messages=True)
        self.config(self.pg2, 'phone-2', enable_whatsapp_messages=True)
        payload = {'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'phone-1'},
            'contacts': [{'wa_id': '919111111111', 'profile': {'name': 'Resident'}}],
            'messages': [{'from': '919111111111', 'id': 'wamid.in', 'timestamp': '1700000000', 'type': 'text', 'text': {'body': 'Hello'}}],
        }}]}]}
        raw = json.dumps(payload, separators=(',', ':')).encode()
        signature = 'sha256=' + hmac.new(b'secret', raw, hashlib.sha256).hexdigest()
        self.assertEqual(process_webhook_payload(payload, raw, signature), 1)
        self.assertEqual(process_webhook_payload(payload, raw, signature), 0)
        self.assertEqual(WhatsAppMessage.objects.filter(pg=self.pg1).count(), 1)
        self.assertFalse(WhatsAppMessage.objects.filter(pg=self.pg2).exists())

    def test_pg_admin_cannot_open_another_pgs_conversation(self):
        self.config(self.pg1, 'phone-1', enable_whatsapp_messages=True)
        self.config(self.pg2, 'phone-2', enable_whatsapp_messages=True)
        from .whatsapp_cloud import get_or_create_conversation
        foreign = get_or_create_conversation(self.pg2, '919222222222', 'PG2 Secret Contact')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('pg_whatsapp_conversations'), {
            'pg': self.pg1.id, 'conversation': foreign.id,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'PG2 Secret Contact')

    def test_webhook_verification_rejects_empty_verify_token(self):
        WhatsAppCloudConfig.objects.create(pg=self.pg1, enabled=True)
        response = self.client.get(reverse('whatsapp_cloud_webhook'), {
            'hub.mode': 'subscribe',
            'hub.verify_token': '',
            'hub.challenge': 'challenge-value',
        })
        self.assertEqual(response.status_code, 403)

    @patch('pgadmin.whatsapp_cloud.requests.post')
    def test_pg_admin_can_search_pg_users_and_send_from_whatsapp_page(self, post):
        self.config(self.pg1, 'phone-1', enable_whatsapp_messages=True)
        tenant = get_user_model().objects.create_user(
            username='tenant', email='tenant@example.com', password='x', first_name='Tenant'
        )
        booking = Booking.objects.create(user=tenant, pg=self.pg1, booking_type=Booking.DAYWISE)
        ResidentApplication.objects.create(
            booking=booking,
            user=tenant,
            pg=self.pg1,
            name='Tenant One',
            phone='99999 99999',
            whatsapp_number='+91 99999 99999',
        )
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'messages': [{'id': 'wamid.page'}]}
        post.return_value = response

        self.client.force_login(self.admin)
        page = self.client.get(reverse('pg_whatsapp_conversations'), {
            'pg': self.pg1.id,
            'contact_q': 'Tenant',
        })
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Tenant One')

        send_response = self.client.post(reverse('pg_whatsapp_conversations'), {
            'pg': self.pg1.id,
            'user_id': tenant.id,
            'message': 'Hello from admin',
        })
        self.assertEqual(send_response.status_code, 302)
        self.assertTrue(WhatsAppMessage.objects.filter(
            pg=self.pg1,
            conversation__contact__user=tenant,
            direction=WhatsAppMessage.OUTBOUND,
            text='Hello from admin',
        ).exists())
        self.assertEqual(post.call_args.kwargs['json']['to'], '919999999999')

    def test_dashboard_replaces_manage_rooms_card_when_whatsapp_enabled(self):
        self.config(self.pg1, 'phone-1', enable_whatsapp_messages=True)
        self.admin.profile.is_pg_admin = True
        self.admin.profile.save(update_fields=['is_pg_admin'])
        self.client.force_login(self.admin)

        response = self.client.get(reverse('dashboard'), {'pg': self.pg1.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'WhatsApp')
        self.assertContains(response, reverse('pg_whatsapp_conversations'))
        self.assertNotContains(response, '>Manage Rooms</a>')

    def test_whatsapp_page_lists_only_current_pg_users(self):
        self.config(self.pg1, 'phone-1', enable_whatsapp_messages=True)
        active_user = get_user_model().objects.create_user(
            username='active', email='active@example.com', password='x'
        )
        inactive_user = get_user_model().objects.create_user(
            username='inactive', email='inactive@example.com', password='x'
        )
        room = Room.objects.create(pg=self.pg1, room_no='A1', total_shares=2)
        active_booking = Booking.objects.create(
            user=active_user, pg=self.pg1, room=room, share_no=1, status=Booking.APPROVED
        )
        inactive_booking = Booking.objects.create(
            user=inactive_user, pg=self.pg1, room=room, share_no=2, status=Booking.COMPLETED
        )
        ResidentApplication.objects.create(
            booking=active_booking,
            user=active_user,
            pg=self.pg1,
            name='Active Tenant',
            whatsapp_number='+91 90000 00001',
        )
        ResidentApplication.objects.create(
            booking=inactive_booking,
            user=inactive_user,
            pg=self.pg1,
            name='Old Tenant',
            whatsapp_number='+91 90000 00002',
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse('pg_whatsapp_conversations'), {'pg': self.pg1.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Active Tenant')
        self.assertNotContains(response, 'Old Tenant')

    @patch('pgadmin.whatsapp_cloud.requests.post')
    def test_invalid_pg_parameter_does_not_fallback_when_sending(self, post):
        self.config(self.pg1, 'phone-1', enable_whatsapp_messages=True)
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'messages': [{'id': 'wamid.invalid-pg'}]}
        post.return_value = response

        self.client.force_login(self.admin)
        response = self.client.post(f"{reverse('pg_whatsapp_cloud_send')}?pg={self.pg2.id}", data=json.dumps({
            'phone': '919999999999',
            'message': 'Should not send',
            'section': 'whatsapp_messages',
        }), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        post.assert_not_called()

    def test_super_admin_configuration_page_handles_unconfigured_pgs(self):
        User = get_user_model()
        super_admin = User.objects.create_superuser(
            username='super', email='super@example.com', password='x'
        )
        self.client.force_login(super_admin)
        response = self.client.get(reverse('sa_whatsapp_cloud_configs'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Default WhatsApp', count=2)
        response = self.client.get(reverse('sa_whatsapp_cloud_config_edit', args=[self.pg1.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('whatsapp_cloud_webhook'))


class PGAdminRoomDeletionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='room-admin',
            email='room-admin@example.com',
            password='x',
        )
        self.pg = PG.objects.create(name='Deletion Test PG', address='One')
        PGAdmin.objects.create(user=self.admin, pg=self.pg)
        self.client.force_login(self.admin)

    def create_room(self, room_no='101'):
        room = Room.objects.create(pg=self.pg, room_no=room_no, total_shares=1)
        RoomShareStatus.objects.create(room=room, share_no=1)
        return room

    def test_unused_room_requires_exact_confirmation_then_deletes(self):
        room = self.create_room()
        url = reverse('pg_room_delete', args=[room.id])

        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'DELETE ROOM 101')

        rejected = self.client.post(url, {'confirmation': 'delete'})
        self.assertEqual(rejected.status_code, 200)
        self.assertTrue(Room.objects.filter(pk=room.id).exists())

        deleted = self.client.post(url, {'confirmation': 'DELETE ROOM 101'})
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(Room.objects.filter(pk=room.id).exists())

    def test_linked_active_booking_is_shown_and_blocks_deletion(self):
        room = self.create_room()
        resident = get_user_model().objects.create_user(
            username='linked-resident',
            email='linked@example.com',
            first_name='Linked',
            last_name='Resident',
        )
        Booking.objects.create(
            user=resident,
            pg=self.pg,
            room=room,
            share_no=1,
            status=Booking.APPROVED,
        )
        url = reverse('pg_room_delete', args=[room.id])

        page = self.client.get(url)
        self.assertContains(page, 'Linked Resident')
        self.assertContains(page, 'This room cannot be deleted yet.')
        self.assertNotContains(page, 'Delete room permanently')

        response = self.client.post(url, {'confirmation': 'DELETE ROOM 101'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Room.objects.filter(pk=room.id).exists())

    def test_pg_admin_cannot_review_another_pgs_room(self):
        other_pg = PG.objects.create(name='Other PG', address='Two')
        room = Room.objects.create(pg=other_pg, room_no='201', total_shares=1)

        response = self.client.get(reverse('pg_room_delete', args=[room.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))

    def test_pg_admin_dashboard_does_not_display_or_mark_notification(self):
        notice = Notification.objects.create(
            user=self.admin,
            title='Hidden dashboard notification',
            message='Use the dedicated notifications page.',
        )

        response = self.client.get(reverse('dashboard'), {'pg': self.pg.id})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Hidden dashboard notification')
        notice.refresh_from_db()
        self.assertFalse(notice.is_read)


class PGAdminTenantDisplayNameTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='tenant-page-admin', email='tenant-admin@example.com', password='x'
        )
        self.resident = User.objects.create_user(
            username='tenant-page-resident',
            email='resident@example.com',
            first_name='AccountFirst',
            last_name='AccountLast',
        )
        self.pg = PG.objects.create(name='Tenant Name PG', address='Test address')
        PGAdmin.objects.create(user=self.admin, pg=self.pg)
        self.room = Room.objects.create(pg=self.pg, room_no='101', total_shares=1)
        RoomShareStatus.objects.create(
            room=self.room, share_no=1, status=RoomShareStatus.OCCUPIED
        )
        self.booking = Booking.objects.create(
            user=self.resident,
            pg=self.pg,
            room=self.room,
            share_no=1,
            status=Booking.APPROVED,
            joining_date=timezone.localdate(),
        )
        ResidentApplication.objects.create(
            user=self.resident,
            booking=self.booking,
            pg=self.pg,
            room=self.room,
            name='Resident Application Name',
            email=self.resident.email,
        )
        self.client.force_login(self.admin)

    def test_tenants_page_prefers_resident_application_name(self):
        response = self.client.get(reverse('pg_tenants'), {'pg': self.pg.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Resident Application Name')
        self.assertNotContains(response, 'AccountFirst AccountLast')
