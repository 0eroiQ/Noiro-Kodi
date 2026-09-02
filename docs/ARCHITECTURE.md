# Architecture

Noiro Kodi is an application layer on top of the supported OSMC/Kodi stack. It
does not ship or modify a bootloader, partition table, kernel or OSMC image.

## Runtime boundaries

```text
skin.noiro / plugin.video.noiro / script.noiro.setup
                         |
                 JSON-RPC v1, mode 0600
                         |
               script.service.noiro
                  /              \
     profile-isolated API      native/noiro-engine
       Stremio + Gemini          pinned Stremio core
                         |
                    Kodi Player
                         |
       OSMC Vero decode, HDR, refresh rate and HDMI audio
```

Python owns Kodi lifecycle integration, profile isolation and the user-facing
routes. The armhf Rust daemon is a versioned bridge to the same pinned Noiro
Stremio core snapshot used by the Apple work, but it has its own Linux process,
storage directory and Unix socket. The Apple project is never modified by this
repository.

The Kodi-facing JSON-RPC v1 service exposes profiles, Stremio linking,
catalog/search/meta/stream/subtitle/library/progress and add-on operations. The
native daemon exposes its own v1 socket for core health, dispatch and state;
this split keeps Kodi/Python lifecycle work out of the native core while still
making the pinned core a separately supervised component.

The release source includes both the versioned Noiro bridge snapshot and an
unmodified copy of the exact pinned upstream `stremio-core` commit, together
with the Debian 11 build container and Rust dependency lock.

## Subtitle path

The selected Stremio subtitle is downloaded into the active Vero profile cache
and attached to Kodi immediately. If that profile enables Gemini translation,
translation runs in a background worker. A validated translated subtitle can
replace the original during playback; timeout, malformed timing or any Gemini
error leaves the original track active.

## Maintenance contract

`maintenance_mode=true` is durable. The service does not force Noiro back on
after a Kodi restart or complete Vero reboot. `Return to Noiro` verifies Kodi
major version 21, all required add-ons, repository service, native engine,
profile database and skin health before clearing maintenance mode.

## Direct/debrid v1

Kodi receives only HTTP(S) URLs that Stremio add-ons have already resolved.
Torrent info-hashes remain visible in Sources but are deliberately not passed
to Kodi. The proprietary Stremio streaming server is not distributed.
