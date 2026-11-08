# Operations handbook

## Open the operations console

Sign in at `/login` with the administrator account you created using the CLI. Supply your authenticator code. Open `/admin/`. Never share an administrator login with a driver: driver links grant only scoped location-posting access.

## Publish the real rate sheet

Open **Rates & zones**. Create a tariff or download the blank import CSV header.

| Field | Interpretation |
| --- | --- |
| `name`, `zone` | Your actual tariff name and zone label |
| `origin`, `destination` | ISO two-letter country codes |
| `origin_city`, `destination_city` | Optional exact city scope, matched case-insensitively; blank explicitly means country-wide |
| `service` | `express`, `economy`, or `freight` |
| `currency` | `KES` or `USD` |
| `max_kg` | Maximum total billable weight covered by this tariff |
| `included_kg`, `base_charge` | Weight covered by the base transportation charge |
| `step_kg`, `step_charge` | Each additional weight increment is rounded upward and charged at this rate |
| `divisor` | Your approved dimensional divisor; do not assume a value without verifying the service agreement |
| `fuel_percent` | Applied to transport charges |
| `handling_charge` | Flat shipment handling charge |
| `tax_percent` | Applied to transport plus fuel plus handling |
| `min_days`, `max_days` | Estimated business-day transit range |
| `valid_from`, `valid_to` | ISO `YYYY-MM-DD` validity window, checked against requested ship date |

All monetary CSV fields are major currency units with at most two decimals. New/imported rates are inactive. Review and publish each row explicitly.

City-specific lanes override country-wide tariffs; if equally specific rows overlap, the newest matching row is considered first. Within a row, each package is billed at the greater of its actual weight and dimensional weight. Those per-package weights are summed before the included-weight and increment calculation. A shipment over the permitted weight receives no automatic rate from that row.

This is a transport rate engine, not a customs duty calculator. Add any known shipment-specific approved charges in the manual quote. Never publish a nationwide rate if only particular domestic cities have been approved. Deactivate old tariffs; use a new row for a changed price so existing quote snapshots remain intact.

## Review a new request

Open **Shipments** and filter `Quote requested`. Review the full customer details, requested pickup, goods description, declared KES value, dimensions, weight, HS/manufacture information and destination. Confirm your actual carrier/route availability and required customs paperwork outside this portal, then issue the quoted transport, fuel, handling and shipping-tax amounts with all relevant inclusions/exclusions.

Manual quotes expire after 48 hours. The customer reviews and accepts before payment. Quotes cannot be changed during an open payment attempt or after settlement.

## Dispatch sequence

Valid main movements are:

- Booking confirmed → collected, warehouse or in transit.
- Collected → warehouse or in transit.
- Warehouse → in transit or customs.
- In transit → warehouse, customs or out for delivery.
- Customs → warehouse, in transit or out for delivery.
- Out for delivery → delivered or warehouse.
- Any active movement may enter an exception; an exception can return to warehouse, in transit or out for delivery after operations resolves it.

A verified payment is required for these changes. Add the actual warehouse/city and a customer-visible note. Do not put private recipient phone numbers, door codes or street addresses into a public timeline note.

A quote request or unpaid quote may be cancelled only when no payment attempt is open. Paid cancellation/refund and return-to-sender require provider/carrier coordination; do not represent an unresolved paid shipment as automatically refunded.

## GPS and delivery

After starting the shipment, issue a driver link as described in GPS_API.md. Each link is valid for this shipment only. If a multi-leg journey changes driver, issue a replacement link; it revokes the previous one. If the journey takes longer than seven days, issue a new link. Record the receiving person's name to confirm delivery. This is staff-recorded proof, not an electronic signature.

## Documents

Customers and administrators may download their authorised shipping quotation or verified receipt. Paid shipments also receive printable A4 handling labels with a QR code, one per package. These are FabsExpress operational documents. Obtain the carrier air waybill, commercial invoice, permits and any statutory electronic tax invoice separately where needed.

## Payments and support

Review **Payments** for held/unknown results. Reconcile with the provider before releasing an attempt for retry. A timeout can still correspond to a real charge; do not assume failure. The admin portal does not fabricate settlement or perform provider refunds.

Support forms create persistent requests and return a reference. Staff reply through the organisation's approved communication channel; clicking a mail link opens the staff member's email client. Mark requests resolved only after handling them. No automated messages are sent to the support team by this application; monitor the support inbox.

**Customers** can enable/disable customer access. Administrative roles are provisioned through the CLI, not through a public form. **Audit trail** records financial and operational actions and is read-only in the app. Database administrators still have database-level privileges; the audit log is not advertised as a tamper-proof external ledger.

## Scheduled work

Keep `python worker.py` running as a separate service. It delivers queued account/shipment emails, verifies pending payments and removes aged GPS points. Failed emails retry with backoff, up to ten attempts. Inspect unresolved rows and provider holds operationally; the worker does not invent success if any provider is unavailable.
