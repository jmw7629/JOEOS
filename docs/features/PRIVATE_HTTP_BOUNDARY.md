# Private HTTP Request Boundary

Status: implemented compatibility-preserving hardening

This boundary reduces DNS-rebinding and cross-site mutation risk while JoeOS remains a single-owner, private-tailnet service. It is not user authentication, device enrollment, RBAC, or permission to expose JoeOS publicly.

## Host policy

JoeOS accepts loopback, RFC 1918, link-local, Tailscale `100.64.0.0/10`, private/link-local IPv6, `.localhost`, `.local`, `.ts.net`, and single-label private hostnames. Malformed, credential-bearing, unspecified, multicast, public-IP, and unapproved public-domain Host headers are rejected before route execution.

An explicit reverse-proxy hostname can be added through comma-separated `JOEOS_ALLOWED_HOSTS`. Wildcards and malformed entries fail closed. Adding a hostname does not make that deployment public-internet ready; the bootstrap security posture remains authoritative until authentication and device enrollment exist.

## Browser mutation policy

State-changing `/api/` requests must use `application/json`. When a browser supplies `Origin`, its hostname and any explicit request port must match the accepted request authority. `Sec-Fetch-Site: cross-site` mutations are rejected. Native clients may omit browser-only Origin and Fetch Metadata headers, but receive no additional authority.

Read requests remain same-origin protected by the absence of CORS and by Host validation. The WebSocket stream retains its separate same-host Origin policy and never accepts commands.

## Request correlation

Every HTTP response receives `X-Request-ID`. A caller-provided ID is retained only when it matches the bounded safe character contract; otherwise JoeOS generates a random UUIDv4 hex value. Rejection bodies include the same ID for operator correlation and never reflect rejected header values.

## Remaining gate

This layer does not identify a person or device. JoeOS must remain on loopback, a private LAN, or a private tailnet until identity, enrollment, short-lived sessions, authorization, approval digests, revocation, and signed runners are implemented and tested.
