# Asset and implementation sources

Reviewed 5 November 2026.

## Branding

Current user-supplied brand reference: https://app.fabsdatawarehouse.com/. The live page used red `#ED3138`, charcoal `#121820` and white. FabsExpress uses a source-native SVG mark and text lockup in that visual family. The parent FABS brand image is retained in `static/images/fabs-brand-v2.png` for future company branding use; the public navigation uses the FabsExpress name.

FedEx Kenya reference: https://www.fedex.com/en-ke/home.html. Used to inform the shipping/rate/tracking information hierarchy. No FedEx logo, proprietary carrier label, affiliation claim or account-specific transportation tariff is embedded.

Rate structure reference: https://www.fedex.com/en-ke/shipping/rates.html. The application supports route/weight pricing and separate charges, but uses only your own approved rates. The dimensional divisor is configured per rate instead of presumed to be the same for every service.

## Photography and provider marks

Files are bundled locally so rendering does not depend on remote hotlinks. They are editorial/stock logistics imagery, not evidence of a FabsExpress-owned fleet or facility.

| Local file | Source |
| --- | --- |
| `static/images/truck.jpg` | https://images.unsplash.com/photo-1519003722824-194d4455a60c?auto=format&fit=crop&w=1800&q=85 |
| `static/images/port.jpg` | https://app.fabsdatawarehouse.com/static/images/operations/port.jpg |
| `static/images/warehouse.jpg` | https://app.fabsdatawarehouse.com/static/images/operations/warehouse.jpg |
| `static/images/fabs-brand-v2.png` | https://app.fabsdatawarehouse.com/static/images/fabs-brand-v2.png |
| `static/images/mpesa.png` | https://app.fabsdatawarehouse.com/static/images/partners/mpesa.png |
| `static/images/dpo.png` | https://app.fabsdatawarehouse.com/static/images/partners/dpo.png |

The parent-brand resources were reused from the website you identified. Keep your rights/permissions for parent-site assets and payment-provider marks. Unsplash photography terms: https://unsplash.com/license. Replace stock photography with your own operational photography when available.

## Bundled components

- Leaflet 1.9.4: https://leafletjs.com/ and its bundled BSD-2-Clause license in `static/vendor/LEAFLET-LICENSE.txt`.
- DejaVu Sans: bundled font files and their license in `static/fonts/LICENSE.txt`, used for PDFs.
- Tailwind CSS 4.1.17: source and package lock included; compiled stylesheet is served locally. https://tailwindcss.com/docs/installation/tailwind-cli
- Flask deployment guidance: https://flask.palletsprojects.com/en/stable/deploying/
- Country names/codes come from the pycountry reference-data package; they are not business activity or coverage claims.

Payment provider sources and setup are listed in PAYMENTS.md. Runtime package versions are pinned in requirements.txt and the frontend build dependencies in package-lock.json.
