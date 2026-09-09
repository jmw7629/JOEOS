# Spotify on Home

The compact player occupies the former Home AI-chat-status corner. It preserves the existing Home cards and navigation and exposes Play, Pause, Stop, the recorded song title/artist, album artwork when available, a Spotify link, and connection settings. It loads no Spotify resources before an explicit account connection (or restoring that connection in the same browser tab).

## Playback

The Web Playback SDK creates a device named `PRFKT_PROJECT · Home`. Play obtains Saxon Shore's albums and singles/EPs from artist `6aGxmrOqjSpDGvIJdId29O`, follows pagination, sorts releases chronologically and tracks by disc/track number, excludes unavailable and other-artist tracks, and deduplicates track IDs/relinked IDs. The complete available queue is passed to this device. Shuffle is disabled and context repeat is requested before playback. A catalog larger than 100 tracks fails explicitly instead of silently truncating it. Availability remains specific to the Spotify account and market.

Pause preserves position. Stop serializes behind any in-flight playback request, pauses, then seeks the current song to zero. Play after Stop resumes that song from the beginning. Normal dashboard navigation preserves the player; workspace sign-out/account changes disconnect it. Playback status/title/artist come from SDK events, not a fabricated song clock. Autoplay denial, Premium/account restrictions, offline devices, request timeouts and rate limits remain visible. Playback requests are not retried blindly.

## Connection

1. In the owner's Spotify developer app, register the exact redirect URI `https://mcso9tqzb9-1.tailb9395f.ts.net/spotify-callback` (or the actual installation origin plus `/spotify-callback`). Local development supports explicit loopback URLs such as `http://127.0.0.1:4193/spotify-callback`; `localhost` and remote plain HTTP are rejected.
2. Open the player's connection menu and enter the app's **public Client ID**. No client secret is used or accepted.
3. Connect Spotify and complete Spotify's own authorization screen. Full browser playback requires Spotify Premium and, for a development app, an account allowed to use that app. Press Play after the browser device is ready.

Authorization uses random state and S256 PKCE. Pending state expires after ten minutes and is bound to a digest of the current workspace session. Access/refresh tokens stay in this tab's session storage, never in URLs, local storage, backend databases or agent traces. The only local-storage value is the public Client ID. Logout/disconnect clears the music session. Token refresh is single-flight and late responses cannot restore a logged-out identity.

The public OAuth return document contains no workspace content and reflects no query values in HTML. It temporarily records the authorization result in this tab, removes the callback query from browser history, then navigates to Home. Home validates the saved state, session binding and expiry before exchanging the one-use code. This accommodates the gateway's unchanged Secure/HttpOnly/SameSite=Strict cookie. The callback sets no authentication cookie and grants no workspace access; callback request URLs are not logged. The gateway's CSP permits the official Spotify SDK, Spotify API/dealer connections and Spotify artwork/media domains; all existing workspace authorization/CSRF checks and frame-ancestor restrictions remain.

## Acceptance and release

`node --test test_spotify.mjs` checks PKCE/state/account/expiry boundaries, token refresh and logout races, bounded request failure, rate-limit non-retry, hostile/cyclic pagination, catalog ordering/deduplication/availability and oversized-catalog rejection. `test_spotify_browser.mjs` runs within the complete Home browser suite using an explicitly simulated Spotify SDK/API. It exercises the real callback, controls, queue/repeat requests, metadata escaping, Stop racing a Play response, navigation/logout and 320/390/768/1440 layouts. It does not prove real Spotify audio, DRM or physical iPhone playback. Gateway tests verify the unauthenticated callback exposes no private data or session cookie. Private-install tests retain installation isolation.

The owner's Spotify browser session was signed out during implementation; no developer app Client ID or live playback authorization was obtained. Live audio acceptance and a scoped gateway/frontend rollout remain pending. No VPS worker was restarted and no existing project ownership was changed. This personal music feature is not a blanket approval for commercial Spotify streaming integrations.

Official references: [Saxon Shore](https://open.spotify.com/artist/6aGxmrOqjSpDGvIJdId29O), [Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk/reference), [PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow), [redirect requirements](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri), [repeat mode](https://developer.spotify.com/documentation/web-api/reference/set-repeat-mode-on-users-playback), and [current development-mode API changes](https://developer.spotify.com/documentation/web-api/references/changes/february-2026).
