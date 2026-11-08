# Real vehicle tracking

## Driver workflow

1. Operations verifies payment and records `In transit` or `Out for delivery` in the shipment admin page.
2. Select **Issue a private driver link**. Copy the one-time displayed link and give it securely to the assigned driver.
3. The driver opens the HTTPS link and selects **Start location sharing**, then grants browser location permission.
4. Keep the page visible and the device awake. Browser background/lock restrictions can stop updates.
5. The customer opens their own tracking page. The marker interpolates briefly between received coordinates; it never advances without a new reading. The line connects recorded observations and is not road routing or a predicted itinerary.
6. On delivery, operations enters the recipient name and saves `Delivered`. The driver token is immediately revoked. It can also be revoked manually at any time.

The link carries a high-entropy token in a URL fragment, which is removed from the address bar after being stored in the tab's session storage. Only its SHA-256 digest is persisted by the server. Each new token replaces the previous token for this shipment. Tokens expire after seven days.

## GPS device / fleet adapter

A continuously operating tracker can send the same readings without a browser. The adapter must use the authorised token from operations and the device's real coordinates; do not generate or interpolate server positions.

`POST /api/driver/location`

| Field | Meaning |
| --- | --- |
| Header `Authorization: Bearer <scoped-token>` | Token issued for this shipment |
| Header `Content-Type: application/json` | JSON payload |
| `latitude` | Actual latitude, -90 to 90 |
| `longitude` | Actual longitude, -180 to 180 |
| `accuracy` | Device-reported horizontal accuracy in metres, 0–500 |
| `timestamp` | Reading time in timezone-aware ISO 8601, normally UTC |

Send a reading around every 10–20 seconds. The endpoint allows 30 requests per minute per source IP. If many fleet devices share an egress IP, use separately scoped device identities and adjust the limiter before increasing fleet throughput. An old reading is ignored, readings more than three minutes from server time are rejected, and a delivered/revoked/expired shipment token is rejected with HTTP 403.

Success returns `status=received` and its timestamp. An out-of-order reading returns `status=ignored`. Invalid coordinates or poor/stale accuracy return 400. Keep device and server clocks synchronized.

## Privacy and map display

`GET /api/tracking/<tracking-number>` returns milestone events publicly. Exact positions are included only when the session belongs to the booking customer or an administrator. Public tracking does not expose contact names, phone numbers or street addresses.

The customer's page polls every 15 seconds while visible. It labels GPS as stale after 120 seconds and retains the last point instead of fabricating motion. Location updates stop after journey completion. A fleet device feed is authenticated, not physically attested; a device vendor's attestation/tamper controls are a separate integration.

Set `MAP_TILE_URL`, `MAP_ORIGIN` and `MAP_ATTRIBUTION` for your authorised XYZ map tile service. Example URL structure is `https://YOUR_PROVIDER/{z}/{x}/{y}.png` plus any provider-approved browser key. The origin must match the actual tile host. No prefetching or offline tile downloading is implemented.

Use a map provider whose usage terms and service level match your operation. If you choose OSM's community tile service, review its current policy and the browser referrer requirements: https://operations.osmfoundation.org/policies/tiles/. Its best-effort availability should not be represented as a contracted SLA.

Detailed GPS points are removed by the worker after `GPS_RETENTION_DAYS` (30 by default). Keep the privacy notice and retention setting aligned with your actual operating policy.
