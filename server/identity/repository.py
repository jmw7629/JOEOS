from __future__ import annotations

import hashlib
import hmac
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from uuid import UUID, uuid5

from .key_protection import IdentityKeyConfigurationError, PairingKeyProtector


@dataclass(frozen=True)
class PairingOfferRecord:
    offer_id: UUID
    server_id: UUID
    audience_origin: str
    pairing_key: bytes = field(repr=False)
    created_at: int
    expires_at: int
    state: str
    claim_count: int


@dataclass(frozen=True)
class EnrollmentChallengeRecord:
    challenge_id: UUID
    request_id: UUID
    request_digest: bytes
    offer_id: UUID
    server_id: UUID
    device_id: UUID
    audience_origin: str
    pairing_key: Optional[bytes] = field(repr=False)
    client_instance_id: UUID
    display_name: str
    platform: str
    os_version: str
    app_version: str
    client_nonce: bytes
    server_nonce: bytes
    auth_public_key: str
    auth_fingerprint: str
    approval_public_key: str
    approval_fingerprint: str
    transcript_digest: bytes
    server_proof: bytes
    auth_payload: bytes
    approval_payload: bytes
    issued_at: int
    expires_at: int
    state: str
    failed_attempts: int


@dataclass(frozen=True)
class EnrolledDeviceRecord:
    device_id: UUID
    enrollment_id: UUID
    server_id: UUID
    credential_id: str
    audience_origin: str
    client_instance_id: UUID
    display_name: str
    platform: str
    os_version: str
    app_version: str
    state: str
    enrolled_at: int
    revoked_at: Optional[int]
    revocation_reason: Optional[str]
    auth_public_key: str
    auth_fingerprint: str
    approval_public_key: str
    approval_fingerprint: str


@dataclass(frozen=True)
class CompletionLookup:
    request_digest: bytes
    device: EnrolledDeviceRecord


class SQLiteDeviceIdentityRepository:
    """Atomic SQLite persistence for local-console device enrollment and revocation."""

    maximum_offer_claims = 5
    maximum_challenge_failures = 5
    _migration_namespace = UUID("03c5365e-7e0a-49a0-81ae-d6f41ff11350")

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        pairing_key_protector: PairingKeyProtector,
    ) -> None:
        self._connection_factory = connection_factory
        self._pairing_key_protector = pairing_key_protector

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS device_pairing_offers (
                    offer_id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    audience_origin TEXT NOT NULL,
                    pairing_key BLOB,
                    secret_digest BLOB NOT NULL UNIQUE CHECK(length(secret_digest) = 32),
                    state TEXT NOT NULL CHECK(state IN (
                        'created', 'claimed', 'completed', 'revoked', 'expired', 'locked'
                    )),
                    claim_count INTEGER NOT NULL DEFAULT 0 CHECK(claim_count >= 0),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL CHECK(expires_at > created_at),
                    consumed_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_device_pairing_offers_state_expiry
                ON device_pairing_offers(state, expires_at);

                CREATE TABLE IF NOT EXISTS device_identity_key_metadata (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    master_key_identifier TEXT NOT NULL CHECK(length(master_key_identifier) = 43)
                );

                CREATE TABLE IF NOT EXISTS device_enrollment_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    request_digest BLOB NOT NULL CHECK(length(request_digest) = 32),
                    offer_id TEXT NOT NULL REFERENCES device_pairing_offers(offer_id),
                    server_id TEXT NOT NULL,
                    device_id TEXT NOT NULL UNIQUE,
                    audience_origin TEXT NOT NULL,
                    client_instance_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    platform TEXT NOT NULL CHECK(platform IN ('ios', 'macos', 'windows', 'linux')),
                    os_version TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    client_nonce BLOB NOT NULL CHECK(length(client_nonce) = 32),
                    server_nonce BLOB NOT NULL UNIQUE CHECK(length(server_nonce) = 32),
                    auth_public_key TEXT NOT NULL,
                    auth_fingerprint TEXT NOT NULL,
                    approval_public_key TEXT NOT NULL,
                    approval_fingerprint TEXT NOT NULL,
                    transcript_digest BLOB NOT NULL CHECK(length(transcript_digest) = 32),
                    server_proof BLOB NOT NULL CHECK(length(server_proof) = 32),
                    auth_payload BLOB NOT NULL,
                    approval_payload BLOB NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'open', 'completed', 'superseded', 'expired', 'locked'
                    )),
                    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts >= 0),
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL CHECK(expires_at > issued_at),
                    completed_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_device_enrollment_challenges_offer_state
                ON device_enrollment_challenges(offer_id, state, expires_at);

                CREATE TABLE IF NOT EXISTS enrolled_devices (
                    device_id TEXT PRIMARY KEY,
                    enrollment_id TEXT NOT NULL UNIQUE,
                    server_id TEXT NOT NULL,
                    credential_id TEXT NOT NULL UNIQUE,
                    audience_origin TEXT NOT NULL,
                    client_instance_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    platform TEXT NOT NULL CHECK(platform IN ('ios', 'macos', 'windows', 'linux')),
                    os_version TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active_unassigned', 'revoked')),
                    enrolled_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    revocation_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_enrolled_devices_state
                ON enrolled_devices(state, enrolled_at DESC);

                CREATE TABLE IF NOT EXISTS enrolled_device_keys (
                    fingerprint TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES enrolled_devices(device_id),
                    purpose TEXT NOT NULL CHECK(purpose IN ('device_authentication', 'approval')),
                    public_key TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK(active IN (0, 1)),
                    created_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    UNIQUE(device_id, purpose)
                );

                CREATE TABLE IF NOT EXISTS device_enrollment_completions (
                    idempotency_key TEXT PRIMARY KEY,
                    request_digest BLOB NOT NULL CHECK(length(request_digest) = 32),
                    challenge_id TEXT NOT NULL UNIQUE REFERENCES device_enrollment_challenges(challenge_id),
                    device_id TEXT NOT NULL UNIQUE REFERENCES enrolled_devices(device_id),
                    completed_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS device_identity_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    offer_id TEXT,
                    challenge_id TEXT,
                    device_id TEXT,
                    occurred_at INTEGER NOT NULL,
                    detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 240)
                );

                CREATE TRIGGER IF NOT EXISTS trg_device_identity_events_no_update
                BEFORE UPDATE ON device_identity_events
                BEGIN
                    SELECT RAISE(ABORT, 'device identity events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_device_identity_events_no_delete
                BEFORE DELETE ON device_identity_events
                BEGIN
                    SELECT RAISE(ABORT, 'device identity events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_enrolled_device_keys_immutable
                BEFORE UPDATE OF fingerprint, device_id, purpose, public_key, created_at
                ON enrolled_device_keys
                BEGIN
                    SELECT RAISE(ABORT, 'enrolled device keys are immutable');
                END;
                """
            )
            self._migrate_challenge_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT master_key_identifier FROM device_identity_key_metadata WHERE singleton = 1"
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO device_identity_key_metadata(singleton, master_key_identifier)
                        VALUES (1, ?)
                        """,
                        (self._pairing_key_protector.identifier,),
                    )
                elif not hmac.compare_digest(
                    str(row["master_key_identifier"]),
                    self._pairing_key_protector.identifier,
                ):
                    raise IdentityKeyConfigurationError(
                        "The configured identity master key does not match this JoeOS database."
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _migrate_challenge_schema(self, connection: sqlite3.Connection) -> None:
        """Upgrade pre-proof challenge rows without leaving usable legacy ceremonies."""

        connection.execute("BEGIN IMMEDIATE")
        try:
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(device_enrollment_challenges)"
                ).fetchall()
            }
            missing = {
                "request_id",
                "request_digest",
                "server_proof",
            } - columns
            if "request_id" in missing:
                connection.execute(
                    "ALTER TABLE device_enrollment_challenges ADD COLUMN request_id TEXT"
                )
            if "request_digest" in missing:
                connection.execute(
                    "ALTER TABLE device_enrollment_challenges ADD COLUMN request_digest BLOB"
                )
            if "server_proof" in missing:
                connection.execute(
                    "ALTER TABLE device_enrollment_challenges ADD COLUMN server_proof BLOB"
                )

            repaired_rows = False
            seen_request_ids = set()
            challenge_rows = connection.execute(
                """
                SELECT challenge_id, request_id, request_digest, server_proof
                FROM device_enrollment_challenges ORDER BY challenge_id
                """
            ).fetchall()
            for row in challenge_rows:
                challenge_id = str(row[0])
                raw_request_id = row[1]
                try:
                    parsed_request_id = UUID(str(raw_request_id))
                    canonical_request_id = str(parsed_request_id)
                    request_id_is_valid = (
                        raw_request_id == canonical_request_id
                        and canonical_request_id not in seen_request_ids
                    )
                except (TypeError, ValueError):
                    request_id_is_valid = False
                    canonical_request_id = ""
                request_digest_is_valid = (
                    isinstance(row[2], bytes) and len(row[2]) == 32
                )
                server_proof_is_valid = (
                    isinstance(row[3], bytes) and len(row[3]) == 32
                )
                if request_id_is_valid and request_digest_is_valid and server_proof_is_valid:
                    seen_request_ids.add(canonical_request_id)
                    continue

                repaired_rows = True
                migrated_request_id = uuid5(
                    self._migration_namespace,
                    "legacy-enrollment-request:" + challenge_id,
                )
                request_digest = hashlib.sha256(
                    b"JOEOS-LEGACY-ENROLLMENT-REQUEST-V1\0"
                    + challenge_id.encode("ascii")
                ).digest()
                server_proof = hashlib.sha256(
                    b"JOEOS-INVALIDATED-LEGACY-SERVER-PROOF-V1\0"
                    + challenge_id.encode("ascii")
                ).digest()
                connection.execute(
                    """
                    UPDATE device_enrollment_challenges
                    SET request_id = ?, request_digest = ?, server_proof = ?,
                        state = CASE WHEN state = 'open' THEN 'superseded' ELSE state END
                    WHERE challenge_id = ?
                    """,
                    (
                        str(migrated_request_id),
                        request_digest,
                        server_proof,
                        challenge_id,
                    ),
                )
                seen_request_ids.add(str(migrated_request_id))

            if missing or repaired_rows:
                connection.execute(
                    """
                    UPDATE device_enrollment_challenges
                    SET state = 'superseded'
                    WHERE state = 'open'
                    """
                )
                connection.execute(
                    """
                    UPDATE device_pairing_offers
                    SET state = 'revoked', pairing_key = NULL,
                        consumed_at = COALESCE(
                            consumed_at,
                            CAST(strftime('%s', 'now') AS INTEGER)
                        )
                    WHERE state IN ('created', 'claimed')
                    """
                )

            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_device_enrollment_challenges_request_id
                ON device_enrollment_challenges(request_id)
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_device_enrollment_challenge_proofs_required_insert
                BEFORE INSERT ON device_enrollment_challenges
                WHEN NEW.request_id IS NULL OR NEW.request_digest IS NULL OR NEW.server_proof IS NULL
                BEGIN
                    SELECT RAISE(ABORT, 'device enrollment challenge proof fields are required');
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_device_enrollment_challenge_proofs_required_update
                BEFORE UPDATE OF request_id, request_digest, server_proof
                ON device_enrollment_challenges
                WHEN NEW.request_id IS NULL OR NEW.request_digest IS NULL OR NEW.server_proof IS NULL
                BEGIN
                    SELECT RAISE(ABORT, 'device enrollment challenge proof fields are required');
                END
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def create_offer(
        self,
        *,
        offer_id: UUID,
        server_id: UUID,
        audience_origin: str,
        pairing_key: bytes,
        secret_digest: bytes,
        created_at: int,
        expires_at: int,
    ) -> None:
        protected_pairing_key = self._pairing_key_protector.protect(
            pairing_key,
            server_id=server_id,
            offer_id=offer_id,
            audience_origin=audience_origin,
        )
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_records(connection, created_at)
            previous = connection.execute(
                """
                SELECT offer_id FROM device_pairing_offers
                WHERE state IN ('created', 'claimed') AND pairing_key IS NOT NULL
                """
            ).fetchall()
            connection.execute(
                """
                UPDATE device_pairing_offers
                SET state = 'revoked', pairing_key = NULL, consumed_at = ?
                WHERE state IN ('created', 'claimed') AND pairing_key IS NOT NULL
                """,
                (created_at,),
            )
            connection.execute(
                """
                UPDATE device_enrollment_challenges
                SET state = 'superseded'
                WHERE state = 'open'
                """
            )
            for row in previous:
                self._event(
                    connection,
                    "pairing_offer.superseded",
                    created_at,
                    offer_id=UUID(str(row["offer_id"])),
                )
            connection.execute(
                """
                INSERT INTO device_pairing_offers(
                    offer_id, server_id, audience_origin, pairing_key, secret_digest,
                    state, claim_count, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'created', 0, ?, ?)
                """,
                (
                    str(offer_id),
                    str(server_id),
                    audience_origin,
                    protected_pairing_key,
                    secret_digest,
                    created_at,
                    expires_at,
                ),
            )
            self._event(connection, "pairing_offer.created", created_at, offer_id=offer_id)
            connection.commit()

    def get_claimable_offer(self, offer_id: UUID, now: int) -> Optional[PairingOfferRecord]:
        self.expire_pending(now)
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM device_pairing_offers
                WHERE offer_id = ? AND state IN ('created', 'claimed')
                  AND expires_at > ? AND pairing_key IS NOT NULL
                  AND claim_count < ?
                """,
                (str(offer_id), now, self.maximum_offer_claims),
            ).fetchone()
        return self._offer(row) if row is not None else None

    def create_challenge(self, record: EnrollmentChallengeRecord, now: int) -> bool:
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_records(connection, now)
            offer = connection.execute(
                """
                SELECT * FROM device_pairing_offers
                WHERE offer_id = ? AND state IN ('created', 'claimed')
                  AND expires_at > ? AND pairing_key IS NOT NULL
                  AND claim_count < ?
                """,
                (str(record.offer_id), now, self.maximum_offer_claims),
            ).fetchone()
            if (
                offer is None
                or offer["server_id"] != str(record.server_id)
                or offer["audience_origin"] != record.audience_origin
                or not hmac.compare_digest(
                    self._pairing_key_from_row(offer),
                    record.pairing_key,
                )
            ):
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE device_enrollment_challenges
                SET state = 'superseded'
                WHERE offer_id = ? AND state = 'open'
                """,
                (str(record.offer_id),),
            )
            connection.execute(
                """
                INSERT INTO device_enrollment_challenges(
                    challenge_id, request_id, request_digest, offer_id, server_id,
                    device_id, audience_origin,
                    client_instance_id, display_name, platform, os_version, app_version,
                    client_nonce, server_nonce, auth_public_key, auth_fingerprint,
                    approval_public_key, approval_fingerprint, transcript_digest,
                    server_proof, auth_payload, approval_payload, state, failed_attempts,
                    issued_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, ?, ?)
                """,
                (
                    str(record.challenge_id),
                    str(record.request_id),
                    record.request_digest,
                    str(record.offer_id),
                    str(record.server_id),
                    str(record.device_id),
                    record.audience_origin,
                    str(record.client_instance_id),
                    record.display_name,
                    record.platform,
                    record.os_version,
                    record.app_version,
                    record.client_nonce,
                    record.server_nonce,
                    record.auth_public_key,
                    record.auth_fingerprint,
                    record.approval_public_key,
                    record.approval_fingerprint,
                    record.transcript_digest,
                    record.server_proof,
                    record.auth_payload,
                    record.approval_payload,
                    record.issued_at,
                    record.expires_at,
                ),
            )
            connection.execute(
                """
                UPDATE device_pairing_offers
                SET state = 'claimed', claim_count = claim_count + 1
                WHERE offer_id = ?
                """,
                (str(record.offer_id),),
            )
            self._event(
                connection,
                "enrollment_challenge.created",
                now,
                offer_id=record.offer_id,
                challenge_id=record.challenge_id,
                device_id=record.device_id,
            )
            connection.commit()
            return True
        except sqlite3.IntegrityError:
            connection.rollback()
            return False
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_challenge_by_request_id(
        self,
        request_id: UUID,
    ) -> Optional[EnrollmentChallengeRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT challenge.*, offer.pairing_key
                FROM device_enrollment_challenges AS challenge
                JOIN device_pairing_offers AS offer ON offer.offer_id = challenge.offer_id
                WHERE challenge.request_id = ?
                """,
                (str(request_id),),
            ).fetchone()
        return self._challenge(row) if row is not None else None

    def get_open_challenge(
        self,
        challenge_id: UUID,
        now: int,
    ) -> Optional[EnrollmentChallengeRecord]:
        self.expire_pending(now)
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT challenge.*, offer.pairing_key
                FROM device_enrollment_challenges AS challenge
                JOIN device_pairing_offers AS offer ON offer.offer_id = challenge.offer_id
                WHERE challenge.challenge_id = ? AND challenge.state = 'open'
                  AND challenge.expires_at > ?
                  AND offer.state = 'claimed' AND offer.expires_at > ?
                  AND offer.pairing_key IS NOT NULL
                """,
                (str(challenge_id), now, now),
            ).fetchone()
        return self._challenge(row) if row is not None else None

    def get_completion(self, idempotency_key: UUID) -> Optional[CompletionLookup]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT completion.request_digest, device.*,
                       auth.public_key AS auth_public_key,
                       auth.fingerprint AS auth_fingerprint,
                       approval.public_key AS approval_public_key,
                       approval.fingerprint AS approval_fingerprint
                FROM device_enrollment_completions AS completion
                JOIN enrolled_devices AS device ON device.device_id = completion.device_id
                JOIN enrolled_device_keys AS auth
                  ON auth.device_id = device.device_id AND auth.purpose = 'device_authentication'
                JOIN enrolled_device_keys AS approval
                  ON approval.device_id = device.device_id AND approval.purpose = 'approval'
                WHERE completion.idempotency_key = ?
                """,
                (str(idempotency_key),),
            ).fetchone()
        if row is None:
            return None
        return CompletionLookup(request_digest=bytes(row["request_digest"]), device=self._device(row))

    def complete_challenge(
        self,
        *,
        challenge: EnrollmentChallengeRecord,
        idempotency_key: UUID,
        request_digest: bytes,
        enrollment_id: UUID,
        credential_id: str,
        completed_at: int,
    ) -> Optional[EnrolledDeviceRecord]:
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_records(connection, completed_at)
            current = connection.execute(
                """
                SELECT challenge.*, offer.pairing_key
                FROM device_enrollment_challenges AS challenge
                JOIN device_pairing_offers AS offer ON offer.offer_id = challenge.offer_id
                WHERE challenge.challenge_id = ? AND challenge.state = 'open'
                  AND challenge.expires_at > ?
                  AND offer.state = 'claimed' AND offer.expires_at > ?
                  AND offer.pairing_key IS NOT NULL
                """,
                (str(challenge.challenge_id), completed_at, completed_at),
            ).fetchone()
            if current is None or not self._same_challenge(self._challenge(current), challenge):
                connection.rollback()
                return None
            connection.execute(
                """
                INSERT INTO enrolled_devices(
                    device_id, enrollment_id, server_id, credential_id, audience_origin,
                    client_instance_id, display_name, platform, os_version, app_version,
                    state, enrolled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active_unassigned', ?)
                """,
                (
                    str(challenge.device_id),
                    str(enrollment_id),
                    str(challenge.server_id),
                    credential_id,
                    challenge.audience_origin,
                    str(challenge.client_instance_id),
                    challenge.display_name,
                    challenge.platform,
                    challenge.os_version,
                    challenge.app_version,
                    completed_at,
                ),
            )
            connection.executemany(
                """
                INSERT INTO enrolled_device_keys(
                    fingerprint, device_id, purpose, public_key, active, created_at
                ) VALUES (?, ?, ?, ?, 1, ?)
                """,
                (
                    (
                        challenge.auth_fingerprint,
                        str(challenge.device_id),
                        "device_authentication",
                        challenge.auth_public_key,
                        completed_at,
                    ),
                    (
                        challenge.approval_fingerprint,
                        str(challenge.device_id),
                        "approval",
                        challenge.approval_public_key,
                        completed_at,
                    ),
                ),
            )
            connection.execute(
                """
                UPDATE device_enrollment_challenges
                SET state = 'completed', completed_at = ?
                WHERE challenge_id = ? AND state = 'open'
                """,
                (completed_at, str(challenge.challenge_id)),
            )
            connection.execute(
                """
                UPDATE device_pairing_offers
                SET state = 'completed', pairing_key = NULL, consumed_at = ?
                WHERE offer_id = ? AND state = 'claimed'
                """,
                (completed_at, str(challenge.offer_id)),
            )
            connection.execute(
                """
                INSERT INTO device_enrollment_completions(
                    idempotency_key, request_digest, challenge_id, device_id, completed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(idempotency_key),
                    request_digest,
                    str(challenge.challenge_id),
                    str(challenge.device_id),
                    completed_at,
                ),
            )
            self._event(
                connection,
                "device.enrolled_unassigned",
                completed_at,
                offer_id=challenge.offer_id,
                challenge_id=challenge.challenge_id,
                device_id=challenge.device_id,
                detail=challenge.platform,
            )
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_device(challenge.device_id)

    def record_failed_completion(self, challenge_id: UUID, now: int) -> None:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT offer_id, failed_attempts FROM device_enrollment_challenges
                WHERE challenge_id = ? AND state = 'open'
                """,
                (str(challenge_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                return
            failures = int(row["failed_attempts"]) + 1
            locked = failures >= self.maximum_challenge_failures
            connection.execute(
                """
                UPDATE device_enrollment_challenges
                SET failed_attempts = ?, state = ?
                WHERE challenge_id = ? AND state = 'open'
                """,
                (failures, "locked" if locked else "open", str(challenge_id)),
            )
            if locked:
                connection.execute(
                    """
                    UPDATE device_pairing_offers
                    SET state = 'locked', pairing_key = NULL
                    WHERE offer_id = ? AND state = 'claimed'
                    """,
                    (row["offer_id"],),
                )
                self._event(
                    connection,
                    "enrollment_challenge.locked",
                    now,
                    offer_id=UUID(str(row["offer_id"])),
                    challenge_id=challenge_id,
                )
            connection.commit()

    def get_device(self, device_id: UUID) -> Optional[EnrolledDeviceRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT device.*,
                       auth.public_key AS auth_public_key,
                       auth.fingerprint AS auth_fingerprint,
                       approval.public_key AS approval_public_key,
                       approval.fingerprint AS approval_fingerprint
                FROM enrolled_devices AS device
                JOIN enrolled_device_keys AS auth
                  ON auth.device_id = device.device_id AND auth.purpose = 'device_authentication'
                JOIN enrolled_device_keys AS approval
                  ON approval.device_id = device.device_id AND approval.purpose = 'approval'
                WHERE device.device_id = ?
                """,
                (str(device_id),),
            ).fetchone()
        return self._device(row) if row is not None else None

    def list_devices(self) -> List[EnrolledDeviceRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT device.*,
                       auth.public_key AS auth_public_key,
                       auth.fingerprint AS auth_fingerprint,
                       approval.public_key AS approval_public_key,
                       approval.fingerprint AS approval_fingerprint
                FROM enrolled_devices AS device
                JOIN enrolled_device_keys AS auth
                  ON auth.device_id = device.device_id AND auth.purpose = 'device_authentication'
                JOIN enrolled_device_keys AS approval
                  ON approval.device_id = device.device_id AND approval.purpose = 'approval'
                ORDER BY device.enrolled_at DESC
                """
            ).fetchall()
        return [self._device(row) for row in rows]

    def revoke_device(self, device_id: UUID, reason: str, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE enrolled_devices
                SET state = 'revoked', revoked_at = ?, revocation_reason = ?
                WHERE device_id = ? AND state = 'active_unassigned'
                """,
                (now, reason, str(device_id)),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE enrolled_device_keys
                SET active = 0, revoked_at = ?
                WHERE device_id = ? AND active = 1
                """,
                (now, str(device_id)),
            )
            self._event(
                connection,
                "device.revoked",
                now,
                device_id=device_id,
                detail=reason,
            )
            connection.commit()
            return True

    def expire_pending(self, now: int) -> int:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired = self._expire_records(connection, now)
            connection.commit()
            return expired

    def validate_pending_key_material(self, now: int) -> int:
        self.expire_pending(now)
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM device_pairing_offers
                WHERE state IN ('created', 'claimed')
                  AND expires_at > ? AND pairing_key IS NOT NULL
                """,
                (now,),
            ).fetchall()
        for row in rows:
            self._pairing_key_from_row(row)
        return len(rows)

    def _expire_records(self, connection: sqlite3.Connection, now: int) -> int:
        challenges = connection.execute(
            """
            UPDATE device_enrollment_challenges
            SET state = 'expired'
            WHERE state = 'open' AND expires_at <= ?
            """,
            (now,),
        )
        offers = connection.execute(
            """
            UPDATE device_pairing_offers
            SET state = 'expired', pairing_key = NULL
            WHERE state IN ('created', 'claimed') AND expires_at <= ?
            """,
            (now,),
        )
        return max(0, challenges.rowcount) + max(0, offers.rowcount)

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        event_type: str,
        occurred_at: int,
        *,
        offer_id: Optional[UUID] = None,
        challenge_id: Optional[UUID] = None,
        device_id: Optional[UUID] = None,
        detail: str = "",
    ) -> None:
        connection.execute(
            """
            INSERT INTO device_identity_events(
                event_type, offer_id, challenge_id, device_id, occurred_at, detail
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                str(offer_id) if offer_id else None,
                str(challenge_id) if challenge_id else None,
                str(device_id) if device_id else None,
                occurred_at,
                detail[:240],
            ),
        )

    def _offer(self, row: sqlite3.Row) -> PairingOfferRecord:
        return PairingOfferRecord(
            offer_id=UUID(str(row["offer_id"])),
            server_id=UUID(str(row["server_id"])),
            audience_origin=str(row["audience_origin"]),
            pairing_key=self._pairing_key_from_row(row),
            created_at=int(row["created_at"]),
            expires_at=int(row["expires_at"]),
            state=str(row["state"]),
            claim_count=int(row["claim_count"]),
        )

    def _challenge(self, row: sqlite3.Row) -> EnrollmentChallengeRecord:
        return EnrollmentChallengeRecord(
            challenge_id=UUID(str(row["challenge_id"])),
            request_id=UUID(str(row["request_id"])),
            request_digest=bytes(row["request_digest"]),
            offer_id=UUID(str(row["offer_id"])),
            server_id=UUID(str(row["server_id"])),
            device_id=UUID(str(row["device_id"])),
            audience_origin=str(row["audience_origin"]),
            pairing_key=(
                self._pairing_key_from_row(row)
                if row["pairing_key"] is not None
                else None
            ),
            client_instance_id=UUID(str(row["client_instance_id"])),
            display_name=str(row["display_name"]),
            platform=str(row["platform"]),
            os_version=str(row["os_version"]),
            app_version=str(row["app_version"]),
            client_nonce=bytes(row["client_nonce"]),
            server_nonce=bytes(row["server_nonce"]),
            auth_public_key=str(row["auth_public_key"]),
            auth_fingerprint=str(row["auth_fingerprint"]),
            approval_public_key=str(row["approval_public_key"]),
            approval_fingerprint=str(row["approval_fingerprint"]),
            transcript_digest=bytes(row["transcript_digest"]),
            server_proof=bytes(row["server_proof"]),
            auth_payload=bytes(row["auth_payload"]),
            approval_payload=bytes(row["approval_payload"]),
            issued_at=int(row["issued_at"]),
            expires_at=int(row["expires_at"]),
            state=str(row["state"]),
            failed_attempts=int(row["failed_attempts"]),
        )

    @staticmethod
    def _device(row: sqlite3.Row) -> EnrolledDeviceRecord:
        return EnrolledDeviceRecord(
            device_id=UUID(str(row["device_id"])),
            enrollment_id=UUID(str(row["enrollment_id"])),
            server_id=UUID(str(row["server_id"])),
            credential_id=str(row["credential_id"]),
            audience_origin=str(row["audience_origin"]),
            client_instance_id=UUID(str(row["client_instance_id"])),
            display_name=str(row["display_name"]),
            platform=str(row["platform"]),
            os_version=str(row["os_version"]),
            app_version=str(row["app_version"]),
            state=str(row["state"]),
            enrolled_at=int(row["enrolled_at"]),
            revoked_at=int(row["revoked_at"]) if row["revoked_at"] is not None else None,
            revocation_reason=(
                str(row["revocation_reason"])
                if row["revocation_reason"] is not None
                else None
            ),
            auth_public_key=str(row["auth_public_key"]),
            auth_fingerprint=str(row["auth_fingerprint"]),
            approval_public_key=str(row["approval_public_key"]),
            approval_fingerprint=str(row["approval_fingerprint"]),
        )

    def _pairing_key_from_row(self, row: sqlite3.Row) -> bytes:
        return self._pairing_key_protector.unprotect(
            bytes(row["pairing_key"]),
            server_id=UUID(str(row["server_id"])),
            offer_id=UUID(str(row["offer_id"])),
            audience_origin=str(row["audience_origin"]),
        )

    @staticmethod
    def _same_challenge(
        current: EnrollmentChallengeRecord,
        expected: EnrollmentChallengeRecord,
    ) -> bool:
        return current == expected
