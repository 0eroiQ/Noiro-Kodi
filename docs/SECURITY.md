# Security model

- GitHub downloads are anonymous because `0eroiQ/Noiro-Kodi` is public. No
  GitHub token is requested, stored, placed in a URL or sent in a header. The
  updater uses direct release-download URLs rather than the rate-limited GitHub
  API; the signed manifest remains the release identity and trust boundary.
- Gemini and Stremio secrets are redacted from Noiro logs. OSMC has no
  hardware keychain, so local secret files are honestly described as
  permission-protected rather than hardware-encrypted; they use mode `0600`.
- The GitHub repository proxy binds to `127.0.0.1`. The first setup listener is a
  temporary LAN page protected by a high-entropy, one-time path and closes
  after provisioning. Because this first alpha uses local HTTP rather than a
  trusted device certificate, provision only on a private trusted LAN; never
  use a public or guest Wi-Fi network for this step.
- Releases use RSA-3072 PKCS#1 v1.5 SHA-256 signatures and per-file SHA-256.
  Verification occurs before a ZIP is exposed to Kodi or staged for update.
- Pro entitlements use a second RSA-3072 key. They are device-bound and
  expiring, and the bearer token is sent only in an Authorization header. The
  release private key and Pro private key are never included in Git or release
  artifacts.
- ZIP extraction rejects absolute paths, traversal and symbolic links.
- Add-on roster installation/removal requires an explicit confirmation and
  preserves the previous account roster for rollback.
- Update installation is blocked during playback. The previous add-on set is
  retained, and two failed boots cause rollback in Estuary.

Report private security issues directly to the repository owner; do not attach
tokens, API keys or Stremio auth keys to an issue.
