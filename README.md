# SeedVest Backend

The backend is the API layer for the SeedVest platform and is built with Django and Django REST Framework. It handles member accounts, group governance, contributions, loans, savings tracking, reporting, notifications, and M-Pesa payment callbacks.

## Core modules

- `accounts/` — authentication, approval, user lifecycle, roles, and access control
- `groups/` — group membership, treasury access, and group-level permission checks
- `finance/` — contributions, penalties, investments, analytics, member financial profile, statements, and report generation
- `payments/` — M-Pesa Daraja integration and callback handling
- `notifications/` — in-app and push notification logic
- `seedvest/` — project configuration, global settings, and URL routing

## Role model

The backend supports roles such as:

- `ADMIN`
- `TREASURER`
- `MEMBER`
- `FINANCIAL_SECRETARY`

The `FINANCIAL_SECRETARY` role is designed for read-only financial oversight and report access for permitted groups and members.

## Financial profile module

The finance module includes member-level profile and history endpoints used by the Flutter mobile app:

- `GET /finance/members/<member_id>/financial-profile/`
- `GET /finance/members/<member_id>/savings-history/`
- `GET /finance/reports/member-statement-pdf/`

These endpoints calculate total savings, active loans, overdue balances, investments, penalties, and net position, then return the data in a contract consumable by the mobile client.

## Getting started

### Prerequisites

- Python 3.11+
- Virtual environment support (`venv`)

### Setup

```bash
cd seedvest_backend
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Create a `.env` file with your project settings, including database credentials, email config, and M-Pesa credentials.

## Admin and setup flow

- Admin users may invite and approve members.
- Password reset and member activation links are delivered through the account workflow.
- Token expiry and user lifecycle behavior are enforced in project settings and account views.

## Testing

Run the full suite:

```bash
python manage.py test
```

Run a specific app:

```bash
python manage.py test <app_name>
```

## Security notes

- JWT access tokens and refresh flows are enforced centrally.
- Refresh tokens may be invalidated on logout and rotation events.
- Sensitive operations remain permission-scoped to the correct role and member access boundaries.
- Reporting and member finance endpoints are protected by approval and access checks before returning sensitive financial data.
