# SeedVest Backend

SeedVest is a micro-investment and savings management platform. This backend provides API services for authentication, governance, finance, notifications, and payments.

## Core Features

- Secure authentication with JWT and refresh-token blacklisting.
- Finance management for contributions, penalties, savings targets, and reporting.
- M-Pesa integration for payment initiation and callback handling.
- Governance workflows for member approval, role management, and auditing.
- Email workflows for activation, admin invites, password reset, and notifications.

## Tech Stack

- Framework: Django, Django REST Framework
- Database: PostgreSQL (configured via `.env`)
- Authentication: SimpleJWT (with token blacklisting)
- Notifications: in-app records, Firebase Cloud Messaging, and Django email delivery

## Getting Started

1. Clone the repository.
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create `.env` in `seedvest_backend/`:
   ```env
   DEBUG=True
   SECRET_KEY=your-secret-key

   DB_NAME=seedvest_db
   DB_USER=seedvest_admin
   DB_PASSWORD=your-db-password
   DB_HOST=localhost
   DB_PORT=5432

   MPESA_CONSUMER_KEY=your-key
   MPESA_CONSUMER_SECRET=your-secret
   MPESA_SHORTCODE=your-shortcode
   MPESA_PASSKEY=your-passkey
   MPESA_CALLBACK_URL=https://your-domain/api/payments/mpesa/callback/

   EMAIL_PROVIDER=smtp
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USE_TLS=True
   EMAIL_HOST_USER=your-email@example.com
   EMAIL_HOST_PASSWORD=your-email-app-password
   DEFAULT_FROM_EMAIL=SeedVest <your-email@example.com>

   RESEND_API_KEY=
   RESEND_FROM_EMAIL=

   FIREBASE_PROJECT_ID=
   FIREBASE_CLIENT_EMAIL=
   FIREBASE_PRIVATE_KEY=

   FRONTEND_URL=seedvest://
   ```
5. Run migrations:
   ```bash
   python manage.py migrate
   ```
6. Create an admin user:
   ```bash
   python manage.py createsuperuser
   ```
   Superusers now receive a membership number automatically on creation.
7. Start the dev server:
   ```bash
   python manage.py runserver
   ```

## Admin Invite and Password Setup Flow

- Admin invites a member with `POST /api/accounts/users/admin_register/`.
- Invited users are created as approved but inactive.
- The system sends a setup email with a secure link to:
  - `GET /reset-password/<uid>/<token>/`
- The user sets a password on that web page; the account activates automatically after success.
- If the setup link expires, admin can resend a new one:
  - `POST /api/accounts/users/{id}/resend-setup-link/`

Note: token expiry is controlled by `PASSWORD_RESET_TIMEOUT` in `seedvest/settings.py` (currently `1800` seconds).

## Testing

Run all tests:
```bash
python manage.py test
```

Run tests for one app:
```bash
python manage.py test <app_name>
```

## Security Notes

- Access tokens are short-lived.
- Refresh tokens are rotated and can be blacklisted on logout.
- Role-based permissions are enforced across governance and finance endpoints.
- Password reset endpoints avoid leaking account existence to clients.
- Notification delivery failures are logged and never reverse successful account or financial operations.
