from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .drive import applicant_drive_filename
from .templatetags.currency import format_indian_number


class ApplicantDriveFilenameTests(SimpleTestCase):
    def test_sanitizes_application_name_and_preserves_safe_extension(self):
        filename = applicant_drive_filename(
            "  Anita D'Souza / R.  ",
            'anita@example.com',
            'aadhaar front',
            'scan FINAL.JPEG',
        )

        self.assertEqual(filename, 'anita-dsouza-r_aadhaar-front.jpeg')

    def test_uses_email_when_name_is_blank(self):
        filename = applicant_drive_filename(
            '', 'resident+booking@example.com', 'selfie', 'camera.png'
        )

        self.assertEqual(filename, 'residentbookingexamplecom_selfie.png')

    def test_uses_email_when_name_has_no_safe_characters(self):
        filename = applicant_drive_filename(
            '!!!', 'fallback@example.com', 'aadhaar', 'document.pdf'
        )

        self.assertEqual(filename, 'fallbackexamplecom_aadhaar.pdf')

    def test_rejects_unsafe_extension_and_uses_safe_default(self):
        filename = applicant_drive_filename(
            'Test User', 'test@example.com', 'selfie', 'photo.bad-extension-too-long', '.jpg'
        )

        self.assertEqual(filename, 'test-user_selfie.jpg')


class IndianCurrencyFormattingTests(SimpleTestCase):
    def test_formats_lakhs_and_crores_with_two_decimals(self):
        self.assertEqual(format_indian_number('12345678.5'), '1,23,45,678.50')

    def test_formats_negative_amount_without_decimals(self):
        self.assertEqual(format_indian_number('-1234567.6', 0), '-12,34,568')

    def test_blank_amount_is_zero(self):
        self.assertEqual(format_indian_number(None), '0.00')


class PublicLandingPageTests(TestCase):
    def test_anonymous_home_renders_marketing_landing_page(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')
        self.assertContains(response, 'The operating system for modern PGs')
        self.assertContains(response, 'Every essential workflow.')
        self.assertContains(response, 'pgms.assistant@gmail.com')
        self.assertContains(response, '+91 8106409810')
        self.assertContains(response, 'PG Staff')
        self.assertNotContains(response, 'Super Admin')
        self.assertContains(response, 'img/landing/operator-avatar.webp')

    def test_authenticated_home_redirects_to_dashboard(self):
        user = get_user_model().objects.create_user(
            username='landing-test-user',
            email='landing@example.com',
            password='test-password',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('home'))

        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
