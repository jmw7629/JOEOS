# JoeOS containers

Container definitions will run PostgreSQL/pgvector, Redis, the control API, and optional model/vector services for reproducible local and production environments. Lemonade and hardware-accelerated local providers may remain host-managed when containerization would reduce GPU/NPU support.

Container networking must keep databases, model APIs, and runners private. Only the authenticated control ingress is client-facing.
