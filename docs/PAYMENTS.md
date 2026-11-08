# M-Pesa and DPO payments

These adapters call production endpoints only. They do not contain success simulators. Do not enter test/sample credentials expecting a payment to succeed.

## M-Pesa

Configure a live Daraja app, production consumer key/secret, the approved shortcode and its STK passkey. Select `CustomerPayBillOnline` for an appropriate Paybill or the transaction type Safaricom approves for your setup. The application validates Kenyan mobile numbers and uses KES.

Endpoints:

- OAuth: `GET https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials`
- STK: `POST https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest`
- Query: `POST https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query`

Each request records its expected amount, phone and payment ID before contacting the provider. The STK response's checkout ID is stored. An unguessable per-attempt callback secret is passed in `CallBackURL`; keep callback URLs out of logs.

The callback endpoint is `/webhooks/mpesa/<payment-public-id>/<per-attempt-secret>`. It validates the secret, expected checkout ID and successful callback amount/phone. It records a deduplicated receipt of the notification and requests an independent Safaricom status query. A callback alone does not authorise dispatch.

The STK query does not supply full receipt details. Provider query success settles only the previously stored checkout attempt and its original amount. Callback metadata adds the receipt when available. If the callback was lost, reconcile the checkout/amount against the provider portal when a formal M-Pesa receipt is required.

M-Pesa payment amounts must be whole KES in this implementation. A quote containing fractional KES is never rounded silently: use DPO or have operations issue a correct approved quote before a new attempt.

## DPO

Use your live `DPO_COMPANY_TOKEN` and a `DPO_SERVICE_TYPE` specifically approved for shipping/logistics. Existing service types for gaming or other businesses are not automatically appropriate for FabsExpress. Ask DPO to activate the relevant logistics service and required currencies.

Endpoint: `POST https://secure.3gdirectpay.com/API/v6/` using XML.

`createToken` includes the quoted amount/currency, a unique local payment reference, service type, logistics description, the return/back URLs and a 30-minute payment link limit. The stored `TransToken` is used for the DPO-hosted card checkout. No card number, CVV or PIN enters FabsExpress forms.

Returning to `/payments/dpo/return/<payment-public-id>` triggers server-to-server `verifyToken`. Query-string payment claims are ignored. The adapter accepts only `Result=000` with the exact expected amount and currency. Authorisation-only, split, partial, under/overpayment or currency conversion differences stay pending/review and cannot release a shipment.

DPO v6 does not guarantee a `CompanyRef` in every verify response; the stored provider token is bound to its original payment record. The application does not select an order from a caller-provided token.

Set an independent random `DPO_CALLBACK_SECRET`, then ask DPO to configure XML pushPayments to:

`https://YOUR_ACTUAL_DOMAIN/webhooks/dpo/YOUR_SECRET`

Supply this URL directly to DPO through your approved merchant configuration/support channel; do not publish it. The endpoint acknowledges a durably recorded notification with `<API3G><Response>OK</Response></API3G>`, and independently verifies the stored token. The worker also checks pending transactions, so a callback is not the only reconciliation path. After the paid state is durably committed, the worker sends `verifyToken` with `VerifyTransaction=1` to acknowledge processing to DPO. Failed acknowledgements remain pending for retry.

## Idempotency and exceptions

- Shipment row locks serialize new checkout attempts on PostgreSQL. Open created/pending/unknown/review attempts block another charge.
- Before payment, the server compares the current quote version and expiry. Amount and currency come from the database, never a browser amount field.
- Timeouts produce an `unknown` hold. Do not repeatedly retry an initiation whose outcome is uncertain.
- Verified settlement is idempotent and creates the booked event once. A late negative status cannot overwrite a verified paid record.
- An unexpected second receipt, a payment after cancellation or a quote mismatch is placed in review. Staff investigates it with the provider.
- **Admin > Payments** can query the provider. It cannot simply mark an unpaid shipment paid.
- A held attempt may be released for retry only after staff records provider investigation evidence confirming no collection or a completed provider refund.
- Refunds are executed in the provider's approved workflow, not by this application. No “refund” button claims to send money.

## Verification sources

- DPO createToken: https://docs.dpopay.com/dpo-pay-by-network/reference/create-token
- DPO verifyToken: https://docs.dpopay.com/dpo-pay-by-network/reference/transaction-status-lookup
- DPO pushPayments: https://docs.dpopay.com/dpo-pay-by-network/reference/pushpayments
- Safaricom Daraja portal: https://developer.safaricom.co.ke/
- Safaricom-owned SDK endpoint/field reference: https://github.com/safaricom/mpesa-php-sdk/blob/master/src/Mpesa.php
- Safaricom-owned callback reference: https://github.com/safaricom/mpesa-php-sdk/blob/master/src/TransactionCallbacks.php

Reviewed 5 November 2026. No live merchant credentials or payments were used during construction; provider activation and authorised real transactions must be validated in your deployed environment.
