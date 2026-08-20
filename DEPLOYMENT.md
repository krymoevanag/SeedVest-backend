# SeedVest deployment runbook

This guide deploys the SeedVest Django API to Render from this backend
repository, connects it to Render PostgreSQL, and configures the Flutter app
to call the deployed API.

It is written for the current `render.yaml` in this directory.

## 1. What this deployment creates

Render creates the following resources from `render.yaml`:

| Resource | Name | Purpose |
| --- | --- | --- |
| Web service | `seedvest-api` | Runs the Django API with Gunicorn. |
| PostgreSQL database | `seedvest-db` | Stores application data. |
| Health check | `/api/health/` | Lets Render verify that the API is running. |

The current Blueprint uses Render's **free** plan for testing. Do not use the
free plan for live financial transactions: it can sleep after inactivity and
the free database is temporary. See [Render's free-tier limitations](https://render.com/docs/free).

For a production launch, change both resources to paid plans before enabling
live M-Pesa payments.

## 2. Security first

Never commit or paste a real `.env` file into GitHub, Render YAML, tickets, or
chat. Only `.env.example` belongs in the repository.

If any credential has been exposed, rotate it before deploying:

- Django `SECRET_KEY`
- PostgreSQL password
- Safaricom Daraja consumer key, consumer secret, shortcode/passkey
- Gmail app password

Use a separate Gmail app password for SeedVest. You can revoke and create app
passwords from your Google Account's [App Passwords settings](https://support.google.com/accounts/answer/185833).

## 3. Pre-deployment checks

From `seedvest_backend/`, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py check --deploy
.\.venv\Scripts\python.exe manage.py test accounts.test_deployment_urls
```

`check --deploy` can warn about a deliberately weak local development secret.
For a production-equivalent check, set a long temporary secret and `DEBUG=False`
for the command. Do not replace the local `.env` secret merely to silence the
warning.

Confirm these files are present:

```text
render.yaml
.python-version
.env.example
requirements.txt
seedvest/settings.py
accounts/url_utils.py
```

## 4. Commit and push safely

Push the deployment changes to the **backend** GitHub repository. The
Blueprint must be at that repository's root (`render.yaml`).

Stage only files you intend to deploy. If `accounts/views.py` contains other
local work, use interactive staging so unrelated changes are not committed:

```powershell
git add requirements.txt seedvest/settings.py .env.example .python-version render.yaml
git add accounts/serializers.py accounts/emails.py accounts/url_utils.py accounts/test_deployment_urls.py
git add -p accounts/views.py
git status
git commit -m "Prepare Render deployment"
git push origin main
```

Before pushing, verify that `.env` does not appear in `git status` and that no
credentials appear in the staged diff:

```powershell
git diff --cached --check
git diff --cached
```

## 5. Create the Render Blueprint

1. Sign in to [Render](https://dashboard.render.com/).
2. Select **New** then **Blueprint**.
3. Connect the `krymoevanag/SeedVest-backend` repository.
4. Choose the `main` branch.
5. Leave **Blueprint Path** as `render.yaml`.
6. Review the resources, then select **Apply**.

Render reads `render.yaml`, creates the web service and database, and prompts
for the environment variables marked `sync: false`. This is the intended
Blueprint workflow for secrets. See [Render Blueprints](https://render.com/docs/blueprint-spec).

### Why migrations run in `startCommand`

Free Render web services do not support `preDeployCommand`. The current
Blueprint therefore runs migrations before Gunicorn starts:

```text
python manage.py migrate --noinput && gunicorn ...
```

For this single-instance test deployment, Django skips migrations that were
already applied. On a paid production service, move migrations back to
`preDeployCommand` before scaling to multiple instances. [Render deployment commands](https://render.com/docs/deploys)

## 6. Enter Render environment variables

Do **not** paste a complete `.env` file into Render. Enter each requested
value separately in the Blueprint form or later in **Service → Environment**.

Once Render shows the service URL, copy it. With the configured service name,
it will normally be:

```text
https://seedvest-api.onrender.com
```

Use the actual URL shown in the Render dashboard if it differs.

| Variable | What to enter |
| --- | --- |
| `DATABASE_URL` | Nothing. It is supplied automatically from `seedvest-db`. |
| `SECRET_KEY` | Nothing. Render generates it because `generateValue: true` is set. |
| `DEBUG` | Already set to `False` in `render.yaml`. Do not change it. |
| `BACKEND_URL` | Already set to the canonical Render URL in `render.yaml`. When using a custom API domain, set `https://YOUR-API-DOMAIN` with no trailing slash. |
| `ALLOWED_HOSTS` | Already set for `seedvest-api.onrender.com`. With a custom API domain, include both API hostnames, comma-separated. |
| `CSRF_TRUSTED_ORIGINS` | Already set for the Render URL. With a custom API domain, include both HTTPS origins, comma-separated. |
| `WEB_CONCURRENCY` | Already set to `2`. |
| `MPESA_CONSUMER_KEY` | Newly rotated Daraja key. |
| `MPESA_CONSUMER_SECRET` | Newly rotated Daraja secret. |
| `MPESA_SHORTCODE` | The matching sandbox or production shortcode. |
| `MPESA_PASSKEY` | The matching sandbox or production passkey. |
| `MPESA_CALLBACK_URL` | Already set to the Render callback URL. With a custom API domain, set `https://YOUR-API-DOMAIN/api/payments/mpesa/callback/`. |
| `EMAIL_HOST` | Your SMTP host, for example `smtp.gmail.com`. |
| `EMAIL_PORT` | Already set to `587`. |
| `EMAIL_USE_TLS` | Already set to `True`. |
| `EMAIL_TIMEOUT` | Already set to `20` seconds. |
| `EMAIL_HOST_USER` | SeedVest sender mailbox address. |
| `EMAIL_HOST_PASSWORD` | A newly generated SMTP/Gmail app password. |
| `DEFAULT_FROM_EMAIL` | Example: `SeedVest <sender@example.com>`. |
| `FRONTEND_URL` | Keep `seedvest://` for Flutter password-reset deep links. |

Do not add the local-only `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, or
`DB_PORT` settings to Render. `DATABASE_URL` replaces them in production.

### Gmail SMTP setup and verification

For a Gmail sender, configure the following values individually in **Service →
Environment**. `EMAIL_HOST_USER` must be the Gmail address that owns the App
Password, and `DEFAULT_FROM_EMAIL` should use that same address.

```text
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-sender@gmail.com
EMAIL_HOST_PASSWORD=your-16-character-Google-App-Password
DEFAULT_FROM_EMAIL=SeedVest <your-sender@gmail.com>
EMAIL_TIMEOUT=20
```

Do not use the normal Gmail account password. Enable two-step verification for
the sender account and create a Google App Password instead.

After deploying, open **Service → Shell** and send one message to an address
you control. A printed `1` means the SMTP server accepted the message; a
traceback in the shell identifies the invalid setting.

```bash
python manage.py shell -c "from django.conf import settings; from django.core.mail import send_mail; print(send_mail('SeedVest SMTP test', 'If you received this, SMTP is configured correctly.', settings.DEFAULT_FROM_EMAIL, ['your-address@example.com'], fail_silently=False))"
```

The API returns a generic success response for password-reset requests to avoid
revealing account existence. If an email cannot be delivered, check the Render
logs for `Email delivery` or `Password reset email delivery failed`.

### Optional custom domain

After adding a custom API domain, add these service variables and redeploy:

```text
BACKEND_URL=https://api.example.com
ALLOWED_HOSTS=api.example.com
CSRF_TRUSTED_ORIGINS=https://api.example.com
MPESA_CALLBACK_URL=https://api.example.com/api/payments/mpesa/callback/
```

Keep `BACKEND_URL` as the public HTTPS API origin, with no `/api/` suffix.

## 7. Verify the first deployment

Open the service's **Logs** page in Render. A successful deployment installs
requirements, collects static files, applies migrations, and starts Gunicorn.

Then open this URL in a browser:

```text
https://YOUR-RENDER-URL/api/health/
```

Expected response:

```json
{"status": "ok"}
```

If it does not return HTTP 200:

1. Open the latest deploy logs.
2. Resolve the first traceback or failed command.
3. Confirm all required environment variables are present.
4. Select **Manual Deploy → Deploy latest commit** after changing settings.

## 8. Create the first administrator

After the service is live, open **Service → Shell** in Render and run:

```bash
python manage.py createsuperuser
```

Open the admin site:

```text
https://YOUR-RENDER-URL/admin/
```

Log in with the administrator account you just created. Do not share this
password or store it in source control.

## 9. Test authentication and email links

Perform these checks before distributing the mobile build:

1. Register a test member.
2. Open the activation email. It must use `https://YOUR-RENDER-URL`, not
   `localhost` or an old ngrok URL.
3. Confirm the account becomes active and pending review.
4. Create an administrator invite and open its setup link.
5. Request a password reset from the mobile app.
6. Confirm the reset email opens the SeedVest app using a link shaped like:

   ```text
   seedvest://reset-password/<uid>/<token>/
   ```

7. Set the new password and verify that login succeeds.

If emails do not arrive, check the Render logs and SMTP credentials. Gmail
requires an app password when using SMTP with two-step verification.

## 10. Configure M-Pesa safely

Start with Daraja sandbox credentials until all tests pass.

1. Set `MPESA_CALLBACK_URL` to the deployed HTTPS callback URL.
2. Initiate one sandbox STK push from the mobile application.
3. Confirm the callback reaches Render in the service logs.
4. Confirm the contribution/payment status updates in the database.
5. Only then apply Safaricom's production onboarding requirements and replace
   every sandbox credential with the matching production value.

Do not point M-Pesa at ngrok after deployment. It must use the public Render
or custom-domain HTTPS URL.

Do not enable live M-Pesa payments on the free service: a sleeping service can
delay or miss time-sensitive callbacks. Move to an always-on paid service
first.

## 11. Configure and release the Flutter app

Create or update `seedvest_mobile/.env` locally. This file is ignored by Git.

```env
ENVIRONMENT=production
API_URL=https://YOUR-RENDER-URL/api/
ENABLE_OFFLINE_MODE=false
ENABLE_DEBUG_LOGGING=false
```

Build and install a test release:

```powershell
cd ..\seedvest_mobile
flutter pub get
flutter build apk --release
```

For Google Play distribution, configure the Android signing key first, then
build an app bundle:

```powershell
flutter build appbundle --release
```

Test the release APK on a physical phone using mobile data, not only on local
Wi-Fi. Verify login, logout, password reset, profile upload, notifications,
and a sandbox M-Pesa flow.

The mobile API client allows up to 90 seconds for its first Render response.
This accommodates a free-service cold start. For consistently fast responses
and reliable payment callbacks, use an always-on paid Render web service.

## 12. Media uploads before real users

The backend currently stores profile pictures and investment attachments under
`MEDIA_ROOT`. Render's service filesystem is ephemeral, so these uploads can
disappear after a deploy or restart. [Render persistent disks](https://render.com/docs/disks)

Before accepting real uploads, move media to durable object storage such as:

- Cloudinary
- Amazon S3
- Supabase Storage

This requires a separate storage integration and migration of any existing
media files. Do not rely on the free Render filesystem for user documents.

## 13. Production launch checklist

Complete every item before enabling production payments:

- [ ] All previously exposed credentials are rotated.
- [ ] Render web service and PostgreSQL database use paid plans.
- [ ] A custom API domain is configured, if required.
- [ ] `BACKEND_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and
      `MPESA_CALLBACK_URL` use the final HTTPS domain.
- [ ] `DEBUG=False`.
- [ ] Health endpoint returns HTTP 200.
- [ ] Django administrator account is created and protected with a strong,
      unique password.
- [ ] Registration, activation, admin invite, and password reset flows work.
- [ ] SMTP email is delivered from the intended sender.
- [ ] M-Pesa sandbox flow has succeeded end-to-end.
- [ ] Safaricom production credentials and callback onboarding are complete.
- [ ] Persistent object storage is configured for uploads.
- [ ] Database backup and restore procedures are documented and tested.
- [ ] Flutter release build is tested on a physical device.

## 14. Ongoing operations and rollback

Each push to `main` triggers a Render deployment because
`autoDeployTrigger: commit` is enabled.

Before pushing a migration:

1. Back up the production database.
2. Run the test suite locally against a safe test database.
3. Review the migration for destructive operations.
4. Deploy during a low-traffic period.
5. Check health, logs, login, and payment callbacks immediately after deploy.

To roll back an application deploy, open the Render service's **Deploys** page
and redeploy the last known-good commit. A code rollback does not automatically
reverse database migrations, so plan database changes separately.

## 15. Common problems

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Blueprint rejects `preDeployCommand` | The web service uses the free plan. | Keep migrations in `startCommand` as this Blueprint does, or upgrade to paid. |
| Render health check fails | Startup command, migrations, or env variable failed. | Read the first error in deploy logs; verify `/api/health/` returns HTTP 200. |
| `DisallowedHost` | Custom domain not in allowed hosts. | Add it to `ALLOWED_HOSTS` and redeploy. |
| Activation email points to localhost/ngrok | `BACKEND_URL` is missing or outdated. | Set it to the final HTTPS API origin. |
| Password reset does not open the app | `FRONTEND_URL` is not `seedvest://`. | Set it to `seedvest://` and redeploy. |
| M-Pesa callback does not arrive | Callback still uses ngrok, invalid credentials, or service is unavailable. | Use the deployed HTTPS callback URL, verify Daraja settings, and inspect Render logs. |
| Profile image disappears after deploy | Local `MEDIA_ROOT` is ephemeral. | Configure object storage before real users upload files. |
| Service is slow after idle time | Free service slept. | Use a paid always-on web service. |

## Reference links

- [Deploy Django on Render](https://render.com/docs/deploy-django)
- [Render Blueprints](https://render.com/docs/blueprint-spec)
- [Render environment variables and secrets](https://render.com/docs/configure-environment-variables)
- [Render deployment commands](https://render.com/docs/deploys)
- [Render free-tier limitations](https://render.com/docs/free)
- [Safaricom Daraja developer portal](https://developer.safaricom.co.ke/)
