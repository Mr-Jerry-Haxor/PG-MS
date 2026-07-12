# PG-MS (Django)

Modern, responsive PG management system. Features will be built incrementally.

## Quick start

1. Create a .env from example and fill secrets.
2. Install dependencies (already added).
3. Run migrations and start server.

```
# PowerShell
./.venv/Scripts/python.exe manage.py migrate
./.venv/Scripts/python.exe manage.py createsuperuser
./.venv/Scripts/python.exe manage.py runserver
```

Visit http://127.0.0.1:8000

## Google Sign-In
- Configure `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.
- Add callback URL in Google console: `http://127.0.0.1:8000/accounts/google/login/callback/`

## WhatsApp Cloud API coexistence

1. Set a stable `WHATSAPP_CREDENTIAL_ENCRYPTION_KEY` in `.env` before saving credentials.
2. Run `manage.py migrate`.
3. Sign in as a Django superuser and open **WhatsApp Cloud**.
4. Configure the PG's Phone Number ID, token, app secret, verify token, endpoints, and section switches.
5. Register the HTTPS callback shown on that page in Meta and subscribe the app to WhatsApp message webhooks.

All PGs remain on the existing WhatsApp links by default. Optional approved template names can be configured per section for messages sent outside Meta's customer-service window.

## Next milestones
- Role strategy (Website Admin, PG Admin, PG User) and basic dashboards
- Room/share CRUD and availability states
- Booking request flow
- Fees setup, payments, expenditures
- Notifications + emails via signals
- Google Drive uploads (selfie, Aadhaar) with preview
