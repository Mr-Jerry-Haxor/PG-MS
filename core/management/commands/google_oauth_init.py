from django.core.management.base import BaseCommand
from django.conf import settings
import os

# Only import when running this command to avoid runtime deps where not needed
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/drive.file']

class Command(BaseCommand):
    help = 'Initialize Google OAuth for personal accounts and save token.json'

    def add_arguments(self, parser):
        parser.add_argument('--client-secrets', required=True, help='Path to OAuth client secrets JSON (from Google Console)')
        parser.add_argument('--token', default=getattr(settings, 'GOOGLE_OAUTH_TOKEN_FILE', 'token.json'), help='Where to write token.json')

    def handle(self, *args, **options):
        secrets = options['client_secrets']
        token_path = options['token']
        if not os.path.exists(secrets):
            self.stderr.write(self.style.ERROR(f'Client secrets not found: {secrets}'))
            return 1
        flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
        self.stdout.write(self.style.SUCCESS(f'OAuth token saved to {token_path}'))
