# Noiro Kodi

NoiroTV for Vero 4K+ keeps OSMC and Kodi as the operating system, hardware
decoder, HDR, refresh-rate and HDMI-audio layer while replacing the normal TV
experience with Noiro profiles, Stremio account data and a Noiro skin.

## Safety boundary

- This project never writes the bootloader, partition table or OSMC image.
- The first release plays direct and debrid-resolved URLs only.
- Raw magnet/torrent results are visible but locked.
- The proprietary Stremio `server.js` is not included.
- The official OSMC skin remains installed and is the recovery/maintenance
  environment.

## Add-ons

| Add-on | Purpose |
| --- | --- |
| `repository.noiro` | Bootstrap, local GitHub proxy and signed-release verification |
| `script.module.noiro` | Shared Python API, storage and security primitives |
| `script.service.noiro` | Startup service, engine supervisor and health/rollback control |
| `plugin.video.noiro` | Home, discovery, search, library, streams and playback routes |
| `script.noiro.setup` | Profiles, Stremio QR linking and maintenance mode |
| `script.noiro.return` | Always-visible Return to Noiro entry in the OSMC skin |
| `skin.noiro` | Remote-first Noiro interface for Kodi 21 |

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_addons.py
sh scripts/build_native.sh
python3 scripts/build_repository.py --require-native
python3 scripts/sign_release.py --private-key /secure/path/release-private.pem
python3 scripts/verify_release.py --require-signature
```

The repository builder creates Kodi-compatible ZIP files, `addons.xml`, its
checksum, a SHA-256 manifest, corresponding source and a single bootstrap ZIP
under `artifacts/`.

The native engine is built in the Debian 11 armhf container so its glibc ABI
matches the OSMC recovery userland. The Apple Noiro repository is not modified.

See [architecture](docs/ARCHITECTURE.md), [security](docs/SECURITY.md), the
[Vero installation guide](docs/VERO_INSTALL.md), and the separate
[physical acceptance gates](docs/ACCEPTANCE.md). The separate
[Noiro Pro contract](docs/PRO.md) documents device linking, signed
entitlements, the no-payment local test server and the production billing
boundary.

## Signed public updates

The public bootstrap repository and direct release assets require no GitHub
account, token or GitHub API quota. Kodi's native repository exposes only the
small `repository.noiro` bootstrap package; its service verifies and installs
the complete Noiro bundle transactionally so tightly-coupled packages cannot
be updated separately.
Every release is still accepted only after RSA signature, Kodi compatibility
and per-file SHA-256 verification. Kodi reaches the repository through a local
loopback proxy, and setup keeps only the optional Gemini key and Maintenance PIN.

Binary releases must include the exact corresponding source, dependency lock,
build instructions, license notices and artifact checksums.

`native/vendor/stremio-core` is an unmodified snapshot of the exact upstream
commit recorded in `native/upstream-core.json`, so the corresponding-source
archive does not depend on a future GitHub checkout remaining available.

Version 0.3.3 is an installable alpha with an integrated widget-first Home,
lightweight Noiro details, playback, search and system dialogs, plus an
official-OSMC-skin maintenance path. Automated checks and the armhf ABI gate
are complete; physical Vero acceptance remains a separate gate for every
signed update.
