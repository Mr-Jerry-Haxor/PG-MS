import hashlib
import hmac
import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
