# FabsExpress

A Flask application for FabsExpress customer shipping and internal logistics operations. Includes the public website, customer accounts, shipment requests, admin quoting and dispatch, actual GPS ingestion, payment integrations, documents and operational records.

**Start with [DEPLOYMENT.md](docs/DEPLOYMENT.md).** Production configuration is intentionally required before the public server starts. The supplied `.env` contains blank credential fields, not working credentials.

## What is included

| Area | Implemented behavior |
| --- | --- |
| Website | Responsive FABS red, white and charcoal branding, real logistics photography, shipping/track/rates controls, service pages, help, privacy and shipping guide |
| Accounts | Registration, email verification, login, password reset, account disabling and session revocation; administrator TOTP |
| Shipping | Domestic, import and export requests; country directory; pickup preferences; contact/address details; up to 20 packages; declared goods value; HS code and manufacture country |
| Rates | Approved country or city lanes; service and weight bands; dimensional weight; transport, fuel, handling and tax components; validity dates; CSV import and published CSV export |
| Quotes | Rate snapshot or administrator-issued quote; 48-hour validity; version checked before payment; no silent price changes |
| Payments | Live Daraja M-Pesa STK Push/query and DPO hosted card checkout/verifyToken; callback handling; duplicate-attempt guard; provider reconciliation; paid status requires verification |
| Operations | Quote review, status transitions, warehouse departure, dispatch, carrier handover references, recipient delivery confirmation, support inbox and audit trail |
| Tracking | Public shipment milestones; private owner/admin GPS; scoped and revocable driver link; browser GPS sharing; authenticated device ingestion; recorded path and stale-signal indication |
| Documents | A4 handling labels with tracking QR, one per package; shipping quotation or verified payment receipt |
| Infrastructure | PostgreSQL-ready SQLAlchemy models and Alembic migrations; Redis-backed limits; durable mail outbox; reconciliation worker; Gunicorn; Dockerfile; Render Blueprint |

There is **no seed command, no bundled database, no demo login, no sample tariff, no fake customer, no generated shipment history and no simulated truck journey**. Automated test fixtures run in a separate disposable in-memory database and never enter the application's database. Test credentials are nonfunctional values restricted to test code.

## How a shipment moves

1. A customer registers and verifies their email.
2. They enter collection/delivery addresses, package details and a preferred collection date.
3. A matching, published tariff produces an actual quote. Otherwise the shipment enters the operations quote queue.
4. Operations confirms the route and charges. The customer reviews and accepts the quote.
5. The customer pays with an activated provider. A request or redirect alone never marks the shipment paid.
6. The payment is verified server-to-server. The booking becomes confirmed.
7. Operations records collection or warehouse receipt, then selects **In transit** to start dispatch.
8. Operations issues a private driver link. The driver starts sharing GPS after giving device permission. An authorised GPS device can use the same scoped ingestion endpoint.
9. The customer tracks actual reported points. No position is invented when a signal is missing.
10. Operations records out-for-delivery and delivery with recipient name. Delivery revokes the driver token.

## Start here

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Fill `.env` with the real settings described in [DEPLOYMENT.md](docs/DEPLOYMENT.md). Generate your own secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The first output is for `SECRET_KEY`; the second is for `DATA_ENCRYPTION_KEY`. Generate an independent long secret for `DPO_CALLBACK_SECRET`. Keep these in your hosting provider's secret configuration. Never paste them into a public repository.

```bash
flask --app wsgi:app db upgrade
flask --app wsgi:app create-admin
flask --app wsgi:app check-config
gunicorn -c gunicorn.conf.py wsgi:app
```

Run the background worker separately using the same environment:

```bash
python worker.py
```

The administrator command prompts for your own name, email, phone and password, then enrolls your authenticator. There are no preset login details.

## Files

- `app.py`: app factory, configuration gates, customer flows, tracking API and payment routes.
- `models.py`: persistent business entities and constraints.
- `auth.py`: authentication, verified accounts, reset tokens and admin MFA.
- `admin.py`: operational, rate, payment and support administration.
- `services/shipping.py`: validation, rate calculation and event logic.
- `services/payments.py`: live provider adapters and verified settlement.
- `services/documents.py`: printable labels and receipts/quotations.
- `worker.py`: email outbox, pending-payment reconciliation and GPS retention.
- `templates/base.html`, `templates/index.html`: shared shell and homepage.
- `static/css/app.css`: visual design; inline design tokens and scripts are also included in templates.
- `static/css/tailwind.css`: precompiled Tailwind; no runtime Tailwind CDN required.
- `migrations/`: versioned schema migrations; no business data inserts.
- `docs/`: deployment, payments, GPS and operations guides.

## Styling changes

The compiled CSS is already included. Only install Node dependencies if changing Tailwind utility classes:

```bash
npm ci
npm run build:css
```

Brand source reviewed: https://app.fabsdatawarehouse.com/ (red `#ED3138`, charcoal `#121820`, white). Shipping action hierarchy is informed by the FedEx Kenya reference, with FabsExpress identity and original content. This application is not presented as a FedEx product or affiliate.

## Verification performed

**42 automated checks passed** for empty installs, public pages, registration/verification/booking, package pricing, city-scope tariffs, ownership checks, admin page rendering, dispatch prerequisites, proof of delivery, payment mismatch/replay protection, checkout idempotency, callback handling, GPS privacy/freshness/precision, CSRF, output escaping, PDFs and account revocation. SQLite migration application and Python/JavaScript syntax checks passed. A desktop browser review confirmed the homepage and shipping tabs; PDF label and quotation layouts were rendered and visually checked.

Live M-Pesa/DPO transactions, SMTP delivery, a real GPS device, real carrier fulfilment, PostgreSQL concurrency under load, backups, and a public Render deployment were **not** exercised in this workspace. Mobile CSS is implemented; a physical mobile-device check remains part of launch acceptance. Complete the launch checks with your actual accounts before accepting customer business.

## Scope boundaries

- Rates belong to FabsExpress. FedEx's account-specific transportation prices were not copied, invented or treated as yours. Import your approved sheet or issue a shipment-specific quote.
- The country list is reference data, not a guarantee that every route is serviceable. Staff confirms coverage, cargo acceptance and carrier arrangements.
- This release manages FabsExpress operations and manually recorded carrier handovers. It does not book FedEx/DHL services, obtain their live account tariffs, purchase their labels or automatically import their tracking events.
- A moving truck requires real GPS readings and an authorised tile provider configuration. Browser GPS may pause when a phone locks; unattended tracking needs a continuously connected GPS device using the documented endpoint.
- Card entry occurs at DPO. M-Pesa currently accepts whole KES amounts; quotes with fractional KES or USD use card checkout. Quote amounts are never silently rounded at payment time.
- Automatic refunds, cash-on-delivery, eTIMS issuance, carrier insurance, electronic signatures and customs filing are not included. Payment exceptions/refunds are handled with the provider, then recorded by operations.
- Labels and receipts are internal FabsExpress shipment documents, not a carrier air waybill, customs commercial invoice or statutory tax invoice.

Read [OPERATIONS.md](docs/OPERATIONS.md), [PAYMENTS.md](docs/PAYMENTS.md) and [GPS_API.md](docs/GPS_API.md) for the exact operating procedures.
