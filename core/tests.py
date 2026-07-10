from django.test import SimpleTestCase

from .drive import applicant_drive_filename


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
