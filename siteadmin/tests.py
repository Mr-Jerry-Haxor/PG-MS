from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from pgadmin.models import PG, PGAdmin


class SuperAdminDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username='super-admin',
            email='super@example.com',
            password='test-password',
        )
        self.regular_user = User.objects.create_user(
            username='regular-user',
            email='regular@example.com',
            password='test-password',
        )

    def test_superuser_can_open_dashboard_and_django_admin_route(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse('super_admin_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Super Admin')
        self.assertContains(response, reverse('admin:index'))
        self.assertEqual(response.context['stats']['users'], get_user_model().objects.count())

    def test_non_superuser_is_redirected(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(reverse('super_admin_dashboard'))

        self.assertRedirects(response, reverse('dashboard'))

    def test_navigation_item_is_visible_only_to_superusers(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('super_admin_dashboard'))
        self.assertContains(response, f'href="{reverse("super_admin_dashboard")}">Super Admin</a>')

        self.client.force_login(self.regular_user)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, f'href="{reverse("super_admin_dashboard")}">Super Admin</a>')


class PGAdminUserPickerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username='picker-superuser', email='picker-admin@example.com', password='test-password'
        )
        self.available_user = User.objects.create_user(
            username='available-user', email='available@example.com', password='test-password',
            first_name='Available', last_name='Person',
        )
        self.assigned_user = User.objects.create_user(
            username='assigned-user', email='assigned@example.com', password='test-password',
        )
        self.pg = PG.objects.create(name='Picker Test PG', address='Test address')
        PGAdmin.objects.create(user=self.assigned_user, pg=self.pg)
        self.client.force_login(self.superuser)

    def test_picker_shows_only_unassigned_users(self):
        response = self.client.get(reverse('sa_pg_admins', args=[self.pg.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="userPicker"')
        self.assertContains(response, 'role="combobox"')
        self.assertContains(response, 'available@example.com')
        self.assertNotContains(response, 'data-email="assigned@example.com"')
        self.assertContains(response, 'id="assignUserButton" type="submit" disabled')

    def test_empty_selection_returns_to_picker_with_message(self):
        response = self.client.post(reverse('sa_pg_admins', args=[self.pg.id]), {})

        self.assertRedirects(response, reverse('sa_pg_admins', args=[self.pg.id]))
        self.assertEqual(PGAdmin.objects.filter(pg=self.pg).count(), 1)
