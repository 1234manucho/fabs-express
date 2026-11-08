# Deploy FabsExpress

## Required real configuration

| Variable | What to supply |
| --- | --- |
| `APP_ENV` | `production` on the public service |
| `SECRET_KEY` | Your own securely generated 48+ character secret |
| `DATA_ENCRYPTION_KEY` | Fernet key; preserves access to encrypted admin MFA secrets; back it up separately |
| `DATABASE_URL` | Managed PostgreSQL connection string; `postgres://`, `postgresql://` and `postgresql+psycopg://` are accepted |
| `REDIS_URL` | Redis/Render Key Value connection URL for shared rate limiting |
| `PUBLIC_BASE_URL` | Exact public HTTPS origin, with no trailing slash or path |
| `TRUST_PROXY` | `true` only when behind exactly one trusted proxy such as the configured Render edge |
| `LEGAL_NAME`, `BUSINESS_ADDRESS`, `SUPPORT_EMAIL` | Your verified operating company information |
| `SUPPORT_PHONE`, `TAX_PIN` | Your real telephone and tax identifier, if applicable |
| `SMTP_HOST`, `SMTP_FROM` | Working outbound email service and verified sender |
| `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Credentials from your email service |
| `SMTP_TLS`, `SMTP_SSL` | STARTTLS on port 587 typically uses `true`, `false`; implicit TLS on 465 uses `false`, `true` |
| `MPESA_*` | Live Daraja credentials and activated shortcode; see PAYMENTS.md |
| `DPO_*` | Live DPO token and logistics-approved service type; see PAYMENTS.md |
| `MAP_TILE_URL`, `MAP_ORIGIN`, `MAP_ATTRIBUTION` | Your authorised HTTPS XYZ tile URL, exact origin and required attribution |
| `GPS_RETENTION_DAYS` | Your chosen GPS retention period; default 30 |

The public server refuses to start without the core production infrastructure, secrets, legal/company contact data and email settings. Missing provider credentials leave those checkout options inactive. Missing map configuration leaves the latest coordinates visible to authorised users with an explicit map activation message; it never invents a location.

Use a browser-safe, origin-restricted tile API key if your tile provider requires one: the tile URL is necessarily sent to the signed-in browser. Never place a privileged server key there. Provider tokens for M-Pesa and DPO remain server-side.

## Render: recommended deployment

The package includes `render.yaml` for a Python web service, one worker, PostgreSQL and Key Value. The file creates infrastructure only when you apply it in your own Render account. Review paid plans, region and backup options in Render before confirming creation.

1. Extract the archive. Put the contents of `FabsExpress/` at the root of a private Git repository.
2. Keep `.env` out of Git; `.gitignore` already excludes it. Commit `.env.example` instead.
3. In Render choose **New > Blueprint**, connect your repository and review `render.yaml`.
4. Supply the requested production settings. `PUBLIC_BASE_URL` must be your actual assigned HTTPS service origin or verified custom domain. Use the same values for the web and worker services; the Blueprint references web environment values from the worker.
5. Set the payment/map credentials when activated. They can remain unconfigured until then. The worker must also receive them for payment reconciliation.
6. Build command: `pip install -r requirements.txt`.
7. Pre-deploy command: `flask --app wsgi:app db upgrade`.
8. Web start command: `gunicorn -c gunicorn.conf.py wsgi:app`.
9. Worker start command: `python worker.py`, with exactly one instance initially.
10. Health check: `/healthz`.
11. After a successful deployment, open the Render web service Shell:

```bash
flask --app wsgi:app create-admin
flask --app wsgi:app check-config
```

Enroll the displayed provisioning URI in your authenticator and enter the current code. Keep the URI private. Use your own password; there is no default password.

If your hosting tier does not support a pre-deploy command, run `flask --app wsgi:app db upgrade` once from the shell before directing traffic to the service. Do not run migrations independently in every Gunicorn worker.

### Manual Render services

If setting up services individually, first create PostgreSQL and Key Value, then a web service and a background worker connected to the same repo. Copy the same environment configuration to both app services. Use internal database/cache connection strings in the same region. Do not use SQLite on an ephemeral Render filesystem.

### Domain changes

Add your intended domain in Render, create the DNS records Render supplies, wait for TLS activation, and update `PUBLIC_BASE_URL` on both services. Confirm DPO redirects and callback configuration use the new domain. Existing emailed links and callback URLs retain the origin used when created, so keep the previous origin available until active requests expire or finish.

## Local development with an empty database

Use this only on your own machine while developing; it is not a customer deployment and does not simulate payments.

```dotenv
APP_ENV=development
DATABASE_URL=sqlite:///fabsexpress.db
REDIS_URL=memory://
PUBLIC_BASE_URL=http://localhost:5000
TRUST_PROXY=false
```

Still set your own `SECRET_KEY` and `DATA_ENCRYPTION_KEY`. Set a working SMTP server for registration verification/password reset. Payment initiation requires HTTPS and complete live credentials. The development server is never used for the production deployment.

```bash
flask --app wsgi:app db upgrade
flask --app wsgi:app create-admin
python app.py
```

The `flask --app wsgi:app init-db` command is an alias for applying migrations; it does not add sample records.

## Docker

```bash
docker build -t fabsexpress .
docker run --rm --env-file .env fabsexpress flask --app wsgi:app db upgrade
docker run --env-file .env -p 10000:10000 fabsexpress
docker run --env-file .env fabsexpress python worker.py
```

Use reachable PostgreSQL/Redis services, an HTTPS reverse proxy and the correct `PUBLIC_BASE_URL`. The image runs as a non-root user and excludes `.env` from the build context. Do not publish an unprotected database/cache port.

## Ongoing operations

- Enable managed PostgreSQL backups and test a restore before launch. Keep `SECRET_KEY` and `DATA_ENCRYPTION_KEY` in a separate protected backup; losing the encryption key requires administrator MFA re-enrollment through controlled database maintenance.
- Deploy schema migrations before code that depends on them. Back up before migration; do not downgrade a live database without a reviewed recovery plan.
- Monitor `/healthz`, worker errors, outbox retry counts and unresolved payment holds.
- Keep exactly one mail/reconciliation worker initially. Email has at-least-once delivery semantics if a process stops after SMTP acceptance and before commit.
- Raw Gunicorn access logging is disabled because callback paths contain secrets. Configure any external proxy/logging platform to redact `/webhooks/*`, `/reset-password/*` and `/verify/*` paths and all Authorization headers.
- Run `flask --app wsgi:app reconcile-payments` for an immediate reconciliation pass; `send-mail` drains the due outbox; `prune-gps` applies your GPS retention period.
- Apply dependency security updates through a checked build and run the tests again. Runtime versions are pinned in `requirements.txt`.

## Launch acceptance

Complete these checks in your deployed environment with your own approved accounts:

1. PostgreSQL migration succeeds and persists records across a redeploy; backup/restore and two concurrent checkout requests are checked on PostgreSQL.
2. Registration verification, password reset and admin authenticator login work through real email delivery.
3. Published tariffs and city scopes match your approved rate sheet. Staff has confirmed serviceability and the customer's accepted carriage/claim terms.
4. A small authorised M-Pesa payment and DPO card payment are verified, shown once, reconciled, and matched to provider receipts. Test declines, cancellation and a delayed callback.
5. A real driver trip sends accurate GPS points; customer isolation, stale signal indication, token revocation and delivery are confirmed on the actual mobile devices.
6. The map provider accepts the production origin, displays correct attribution and has an appropriate capacity/service arrangement.
7. Verify phone layouts, printed labels, support contacts, legal operator information, retention settings and support handling before directing customers to the site.

Reference: https://render.com/docs/deploy-flask and https://render.com/docs/blueprint-spec (reviewed 5 November 2026).
