# JoeOS shared contracts

Contract rules:

- Every public schema has a stable name and explicit version.
- Syncable records include workspace, origin node, revision, timestamps, and tombstone semantics.
- Events include event ID, type, workspace, aggregate, sequence/cursor, correlation, causation, time, and typed payload.
- Commands declare capability, normalized arguments, risk tier, actor/device context, and idempotency key.
- Unknown fields and incompatible versions fail closed at execution and approval boundaries.
