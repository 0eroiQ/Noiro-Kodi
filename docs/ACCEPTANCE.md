# Physical acceptance gates

Automated tests establish storage isolation, updater safety and API behavior;
they do not claim hardware acceptance. On the real Vero 4K+, record each gate:

- cold boot opens the profile picker;
- separate profiles show separate Stremio Library/progress/add-ons;
- QR link pending, expiry and successful account validation;
- direct and debrid playback; raw torrent rows visible but locked;
- 4K HEVC 10-bit, HDR, refresh switching and HDMI passthrough through Kodi;
- progress at 20-second intervals, pause, stop and end;
- Gemini translation cache and immediate original-subtitle fallback;
- update confirmation, reboot health confirmation and forced rollback test;
- Maintenance PIN, persistent Estuary reboot, OSMC update and Return to Noiro;
- no bootloader, partition or OSMC-image write.
