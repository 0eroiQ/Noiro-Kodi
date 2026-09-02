# Vero 4K+ installation

1. Keep the supported OSMC/Kodi 21 installation working.
2. Copy only `repository.noiro.zip` to a USB drive.
3. In Estuary, enable installation from unknown sources and choose **Install
   from zip file**.
4. Scan the one-time Noiro setup QR. Enter a fine-grained GitHub token scoped
   only to this private repository, an optional Gemini key, and a four-digit
   Maintenance PIN.
5. Noiro verifies the signed private release, installs the remaining packages,
   performs its health check, and only then activates the Noiro skin.

The first release intentionally never asks to flash an image, open recovery,
hold reset, or write an OSMC partition.

## Recovery to normal Kodi

Choose **Settings → Maintenance**, enter the PIN, and select **Open OSMC/Kodi**.
Estuary remains active across restarts. In Estuary, **Program add-ons → Return
to Noiro** performs compatibility and health checks before restoring Noiro.
