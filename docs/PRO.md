# Noiro Pro account and entitlement foundation

Noiro Pro is a service entitlement, not a secret Kodi ZIP. The public GPL
client can be inspected and redistributed; paid cloud services must therefore
enforce authorization on the server as well as showing a signed status in the
client. Existing local playback, profiles, Stremio linking and a user-supplied
Gemini key remain available without Pro.

## Vero device-link flow

The Vero never accepts card details. It requests a short-lived device code,
shows the verification address and code on the TV, and polls at the interval
chosen by the server.

1. `POST /v1/device-links` with `device_id`, `device_name` and `platform`.
2. The server returns `device_code`, `user_code`, `verification_uri`,
   `verification_uri_complete`, `expires_in` and `interval`.
3. `GET /v1/device-links/{device_code}` returns pending status `101`, expired,
   or a linked bearer token.
4. `GET /v1/entitlements/current` uses that token only in the Authorization
   header and returns a signed entitlement envelope.

The token is never placed in a URL or Kodi log. OSMC has no hardware keychain,
so the device stores it in the existing mode-`0600` local secret file. A Noiro
account is device-wide and does not replace or merge any profile's Stremio
token.

## Signed entitlement

The server signs canonical UTF-8 JSON (`sort_keys=True`, compact separators)
from the `payload` field with a dedicated RSA-3072 PKCS#1 v1.5 SHA-256 key.
The release-signing key is deliberately different. The Vero checks the
signature, device binding, issue time, expiry, plan and feature list before it
shows Pro.

The private entitlement key lives outside Git and outside release artifacts at
`~/.config/noiro-kodi/pro-entitlement-private.pem`. Only
`resources/pro_public_key.json` is distributed. Key rotation must ship a new
trusted public key before the server starts using its corresponding private
key.

## Local end-to-end test

The reference server issues test entitlements and never charges money:

```bash
python3 scripts/generate_pro_key.py
python3 cloud/noiro-pro-server/server.py --bind 0.0.0.0 --port 8098
```

On a private home LAN, set the Vero's Noiro account service address to the
Mac's LAN address, for example `http://192.168.4.20:8098`. Open the displayed
address on a phone, enter the TV code and select **Activate test Pro**. Plain
HTTP is accepted by the client only for loopback, `.local` or private-IP test
servers. Production requires HTTPS.

## Production payment boundary

The reference server is not a billing server. A production implementation
should use a hosted checkout page and customer portal (for example Stripe
Checkout), keeping all provider secrets on the server. The payment provider's
signed webhook—not a browser redirect—changes the subscription record. The
entitlement endpoint then derives plan/features from that record and signs a
short-lived device-bound result.

Required production controls include authenticated account ownership,
idempotent webhook processing, replay protection, subscription cancellation
and grace-period rules, device revocation, audit records, rate limits, TLS,
database backups and a privacy/terms flow. No real checkout should be enabled
until those controls and the business account are configured and tested.

## First active gate

Version 0.2.0 uses `pro_badge` as the first harmless, testable gated feature.
`pro_preview` reserves access to future preview screens but does not claim that
cloud sync or managed translation already exists. Existing Free behavior is
unchanged if the account service is offline, the token is removed or an
entitlement expires.
