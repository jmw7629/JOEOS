import hashlib
import hmac
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import joeos_backend as backend

from server.identity.crypto import (
    P256_ORDER,
    base64url_decode,
    base64url_encode,
    encode_p256_public_key,
)
from server.identity.enrollment_models import (
    EnrollmentChallengeRequest,
    EnrollmentCompletionRequest,
    EnrollmentDeviceMetadata,
    EnrollmentPublicKey,
)
from server.identity.key_protection import (
    IdentityKeyConfigurationError,
    PairingKeyProtectionError,
    PairingKeyProtector,
)
from server.identity.repository import SQLiteDeviceIdentityRepository
from server.identity.router import router
from server.identity.service import (
    CLIENT_PROOF_DOMAIN,
    SERVER_PROOF_DOMAIN,
    DeviceEnrollmentService,
    EnrollmentConflictError,
    EnrollmentOriginError,
    EnrollmentProtocolError,
)


SERVER_ID = UUID("12345678-1234-4abc-8def-1234567890ab")
TAILSCALE_ORIGIN = "http://100.98.25.26:8080"


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class BeginBarrierConnection:
    """Test wrapper that makes two writers attempt each immediate transaction together."""

    def __init__(self, connection, barrier):
        self._connection = connection
        self._barrier = barrier

    def execute(self, statement, *arguments):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            self._barrier.wait(timeout=10)
        return self._connection.execute(statement, *arguments)

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, *arguments):
        return self._connection.__exit__(*arguments)


class DeviceEnrollmentFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "identity.db"
        self.clock = MutableClock()

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.connect = connect
        self.master_key = bytes(range(32))
        self.repository = SQLiteDeviceIdentityRepository(
            connect,
            PairingKeyProtector(self.master_key),
        )
        self.service = DeviceEnrollmentService(
            repository=self.repository,
            server_id_provider=lambda: SERVER_ID,
            now_provider=self.clock,
        )
        self.service.prepare()
        self.authentication_key = ec.generate_private_key(ec.SECP256R1())
        self.approval_key = ec.generate_private_key(ec.SECP256R1())

    def tearDown(self):
        self.tempdir.cleanup()

    def challenge_request(
        self,
        offer,
        origin=TAILSCALE_ORIGIN,
        request_id=None,
        observed_server_id=SERVER_ID,
    ):
        authentication_key = EnrollmentPublicKey(
            value=encode_p256_public_key(self.authentication_key.public_key())
        )
        approval_key = EnrollmentPublicKey(
            value=encode_p256_public_key(self.approval_key.public_key())
        )
        request = EnrollmentChallengeRequest(
            request_id=request_id or uuid4(),
            offer_id=offer.offer_id,
            observed_server_id=observed_server_id,
            audience_origin=origin,
            client_nonce=base64url_encode(bytes(range(32))),
            device=EnrollmentDeviceMetadata(
                client_instance_id=uuid4(),
                display_name="Joe's iPhone",
                platform="ios",
                os_version="17.6",
                app_version="1.0.0",
            ),
            device_authentication_key=authentication_key,
            approval_key=approval_key,
            claim_proof=base64url_encode(b"\0" * 32),
        )
        _, _, pairing_key = self.service.derive_pairing_key_from_manual_code(
            offer.manual_code
        )
        return request.model_copy(
            update={"claim_proof": self.service.claim_proof(pairing_key, request)}
        )

    @staticmethod
    def signature(private_key, payload):
        value = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
        return base64url_encode(value)

    def completion(self, offer, challenge, idempotency_key=None, client_proof=None):
        _, parsed_offer_id, pairing_key = self.service.derive_pairing_key_from_manual_code(
            offer.manual_code
        )
        self.assertEqual(parsed_offer_id, offer.offer_id)
        digest = base64url_decode(challenge.transcript_sha256, expected_decoded_length=32)
        proof = client_proof or self.service.client_proof(pairing_key, digest)
        return EnrollmentCompletionRequest(
            idempotency_key=idempotency_key or uuid4(),
            transcript_sha256=challenge.transcript_sha256,
            client_proof=proof,
            device_authentication_signature=self.signature(
                self.authentication_key,
                base64url_decode(challenge.device_authentication_payload),
            ),
            approval_signature=self.signature(
                self.approval_key,
                base64url_decode(challenge.approval_payload),
            ),
        )


class DeviceIdentitySchemaMigrationTests(unittest.TestCase):
    def test_pre_proof_database_is_upgraded_and_pending_ceremonies_are_invalidated(self):
        with tempfile.TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "legacy-identity.db"

            def connect():
                connection = sqlite3.connect(str(database), timeout=10)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                return connection

            offer_id = uuid4()
            challenge_id = uuid4()
            with connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE device_pairing_offers (
                        offer_id TEXT PRIMARY KEY,
                        server_id TEXT NOT NULL,
                        audience_origin TEXT NOT NULL,
                        pairing_key BLOB,
                        secret_digest BLOB NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        claim_count INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        consumed_at INTEGER
                    );
                    CREATE TABLE device_enrollment_challenges (
                        challenge_id TEXT PRIMARY KEY,
                        offer_id TEXT NOT NULL REFERENCES device_pairing_offers(offer_id),
                        server_id TEXT NOT NULL,
                        device_id TEXT NOT NULL UNIQUE,
                        audience_origin TEXT NOT NULL,
                        client_instance_id TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        os_version TEXT NOT NULL,
                        app_version TEXT NOT NULL,
                        client_nonce BLOB NOT NULL,
                        server_nonce BLOB NOT NULL UNIQUE,
                        auth_public_key TEXT NOT NULL,
                        auth_fingerprint TEXT NOT NULL,
                        approval_public_key TEXT NOT NULL,
                        approval_fingerprint TEXT NOT NULL,
                        transcript_digest BLOB NOT NULL,
                        auth_payload BLOB NOT NULL,
                        approval_payload BLOB NOT NULL,
                        state TEXT NOT NULL,
                        failed_attempts INTEGER NOT NULL DEFAULT 0,
                        issued_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        completed_at INTEGER
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO device_pairing_offers(
                        offer_id, server_id, audience_origin, pairing_key, secret_digest,
                        state, claim_count, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, 'claimed', 1, 1, 9999999999)
                    """,
                    (str(offer_id), str(SERVER_ID), TAILSCALE_ORIGIN, b"legacy", b"s" * 32),
                )
                connection.execute(
                    """
                    INSERT INTO device_enrollment_challenges(
                        challenge_id, offer_id, server_id, device_id, audience_origin,
                        client_instance_id, display_name, platform, os_version, app_version,
                        client_nonce, server_nonce, auth_public_key, auth_fingerprint,
                        approval_public_key, approval_fingerprint, transcript_digest,
                        auth_payload, approval_payload, state, failed_attempts, issued_at,
                        expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'Legacy iPhone', 'ios', '17', '1',
                              ?, ?, 'auth', 'auth-fingerprint', 'approval',
                              'approval-fingerprint', ?, ?, ?, 'open', 0, 1, 9999999999)
                    """,
                    (
                        str(challenge_id),
                        str(offer_id),
                        str(SERVER_ID),
                        str(uuid4()),
                        TAILSCALE_ORIGIN,
                        str(uuid4()),
                        b"c" * 32,
                        b"n" * 32,
                        b"t" * 32,
                        b"auth-payload",
                        b"approval-payload",
                    ),
                )

            repository = SQLiteDeviceIdentityRepository(
                connect,
                PairingKeyProtector(bytes(range(32))),
            )
            repository.prepare()

            with connect() as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(device_enrollment_challenges)"
                    )
                }
                challenge = connection.execute(
                    """
                    SELECT request_id, request_digest, server_proof, state
                    FROM device_enrollment_challenges WHERE challenge_id = ?
                    """,
                    (str(challenge_id),),
                ).fetchone()
                offer = connection.execute(
                    "SELECT state, pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                    (str(offer_id),),
                ).fetchone()

            self.assertTrue({"request_id", "request_digest", "server_proof"} <= columns)
            self.assertEqual(UUID(challenge["request_id"]).version, 5)
            self.assertEqual(len(challenge["request_digest"]), 32)
            self.assertEqual(len(challenge["server_proof"]), 32)
            self.assertEqual(challenge["state"], "superseded")
            self.assertEqual(offer["state"], "revoked")
            self.assertIsNone(offer["pairing_key"])

    def test_two_processes_converge_on_one_legacy_schema_upgrade(self):
        with tempfile.TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "concurrent-legacy-identity.db"
            with sqlite3.connect(str(database)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE device_pairing_offers (
                        offer_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        pairing_key BLOB,
                        expires_at INTEGER NOT NULL,
                        consumed_at INTEGER
                    );
                    CREATE TABLE device_enrollment_challenges (
                        challenge_id TEXT PRIMARY KEY,
                        offer_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        expires_at INTEGER NOT NULL
                    );
                    """
                )

            barrier = Barrier(2)

            def connect():
                raw = sqlite3.connect(str(database), timeout=10)
                raw.row_factory = sqlite3.Row
                raw.execute("PRAGMA foreign_keys = ON")
                raw.execute("PRAGMA busy_timeout = 10000")
                return BeginBarrierConnection(raw, barrier)

            repositories = (
                SQLiteDeviceIdentityRepository(
                    connect,
                    PairingKeyProtector(bytes(range(32))),
                ),
                SQLiteDeviceIdentityRepository(
                    connect,
                    PairingKeyProtector(bytes(range(32))),
                ),
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(repository.prepare) for repository in repositories]
                for future in futures:
                    future.result(timeout=20)

            with sqlite3.connect(str(database)) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(device_enrollment_challenges)"
                    )
                }
                metadata_count = connection.execute(
                    "SELECT COUNT(*) FROM device_identity_key_metadata"
                ).fetchone()[0]
            self.assertTrue({"request_id", "request_digest", "server_proof"} <= columns)
            self.assertEqual(metadata_count, 1)


class DeviceEnrollmentServiceTests(DeviceEnrollmentFixture):
    def test_claim_proof_binds_server_origin_metadata_and_both_keys(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        request = self.challenge_request(offer)
        for changed in (
            request.model_copy(update={"claim_proof": base64url_encode(b"x" * 32)}),
            request.model_copy(update={"observed_server_id": uuid4()}),
        ):
            with self.subTest(field=changed), self.assertRaises(EnrollmentProtocolError):
                self.service.create_challenge(changed)
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM device_enrollment_challenges"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_exact_challenge_retry_is_idempotent_and_changed_reuse_conflicts(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        request = self.challenge_request(offer)

        with ThreadPoolExecutor(max_workers=2) as executor:
            challenges = list(executor.map(self.service.create_challenge, (request, request)))
        self.assertEqual(challenges[0], challenges[1])

        changed = request.model_copy(
            update={"claim_proof": base64url_encode(b"z" * 32)}
        )
        with self.assertRaises(EnrollmentConflictError):
            self.service.create_challenge(changed)
        with self.connect() as connection:
            challenge_count = connection.execute(
                "SELECT COUNT(*) FROM device_enrollment_challenges"
            ).fetchone()[0]
            claim_count = connection.execute(
                "SELECT claim_count FROM device_pairing_offers WHERE offer_id = ?",
                (str(offer.offer_id),),
            ).fetchone()[0]
        self.assertEqual(challenge_count, 1)
        self.assertEqual(claim_count, 1)

    def test_concurrent_changed_request_id_reuse_has_one_winner_and_one_conflict(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        original = self.challenge_request(offer)
        changed = original.model_copy(
            update={
                "device": original.device.model_copy(update={"display_name": "Changed iPhone"})
            }
        )
        _, _, pairing_key = self.service.derive_pairing_key_from_manual_code(
            offer.manual_code
        )
        changed = changed.model_copy(
            update={"claim_proof": self.service.claim_proof(pairing_key, changed)}
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.service.create_challenge, request)
                for request in (original, changed)
            ]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as error:
                errors.append(error)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], EnrollmentConflictError)
        with self.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM device_enrollment_challenges"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT claim_count FROM device_pairing_offers WHERE offer_id = ?",
                    (str(offer.offer_id),),
                ).fetchone()[0],
                1,
            )

    def test_repository_rejects_a_mismatched_runtime_master_key_identifier(self):
        mismatched_repository = SQLiteDeviceIdentityRepository(
            self.connect,
            PairingKeyProtector(b"x" * 32),
        )

        with self.assertRaises(IdentityKeyConfigurationError) as raised:
            mismatched_repository.prepare()

        self.assertIn("does not match", str(raised.exception))
        with self.connect() as connection:
            stored_identifier = connection.execute(
                "SELECT master_key_identifier FROM device_identity_key_metadata WHERE singleton = 1"
            ).fetchone()[0]
        self.assertEqual(
            stored_identifier,
            PairingKeyProtector(self.master_key).identifier,
        )

    def test_second_local_offer_supersedes_and_scrubs_the_first(self):
        first = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        first_challenge = self.service.create_challenge(self.challenge_request(first))
        second = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)

        with self.connect() as connection:
            first_row = connection.execute(
                "SELECT state, pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                (str(first.offer_id),),
            ).fetchone()
            second_row = connection.execute(
                "SELECT state, pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                (str(second.offer_id),),
            ).fetchone()
            challenge_state = connection.execute(
                "SELECT state FROM device_enrollment_challenges WHERE challenge_id = ?",
                (str(first_challenge.challenge_id),),
            ).fetchone()[0]
            superseded_events = connection.execute(
                """
                SELECT COUNT(*) FROM device_identity_events
                WHERE event_type = 'pairing_offer.superseded' AND offer_id = ?
                """,
                (str(first.offer_id),),
            ).fetchone()[0]

        now = int(self.clock().timestamp())
        self.assertEqual(first_row["state"], "revoked")
        self.assertIsNone(first_row["pairing_key"])
        self.assertEqual(challenge_state, "superseded")
        self.assertIsNone(self.repository.get_claimable_offer(first.offer_id, now))
        self.assertEqual(second_row["state"], "created")
        self.assertIsNotNone(second_row["pairing_key"])
        self.assertIsNotNone(self.repository.get_claimable_offer(second.offer_id, now))
        self.assertEqual(superseded_events, 1)

    def test_tampered_protected_pairing_blob_fails_closed(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        with self.connect() as connection:
            protected = bytearray(
                connection.execute(
                    "SELECT pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                    (str(offer.offer_id),),
                ).fetchone()[0]
            )
            protected[-1] ^= 1
            connection.execute(
                "UPDATE device_pairing_offers SET pairing_key = ? WHERE offer_id = ?",
                (bytes(protected), str(offer.offer_id)),
            )

        with self.assertRaises(PairingKeyProtectionError):
            self.service.create_challenge(self.challenge_request(offer))
        with self.connect() as connection:
            challenge_count = connection.execute(
                "SELECT COUNT(*) FROM device_enrollment_challenges WHERE offer_id = ?",
                (str(offer.offer_id),),
            ).fetchone()[0]
        self.assertEqual(challenge_count, 0)

    def test_pairing_material_is_encrypted_at_rest_and_redacted_from_reprs(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        _, _, pairing_key = self.service.derive_pairing_key_from_manual_code(offer.manual_code)
        with self.connect() as connection:
            stored = bytes(
                connection.execute(
                    "SELECT pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                    (str(offer.offer_id),),
                ).fetchone()[0]
            )
        record = self.repository.get_claimable_offer(offer.offer_id, int(self.clock().timestamp()))

        self.assertNotEqual(stored, pairing_key)
        self.assertNotIn(pairing_key, stored)
        self.assertNotIn(offer.manual_code, repr(offer))
        self.assertNotIn("pairing_key", repr(record))

    def test_full_pairing_proves_secret_and_two_keys_without_granting_authority(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        origin, parsed_offer_id, pairing_key = self.service.derive_pairing_key_from_manual_code(
            offer.manual_code
        )
        request = self.challenge_request(offer)
        challenge = self.service.create_challenge(request)
        digest = base64url_decode(challenge.transcript_sha256, expected_decoded_length=32)
        expected_server_proof = hmac.new(
            pairing_key,
            SERVER_PROOF_DOMAIN + digest,
            hashlib.sha256,
        ).digest()

        self.assertEqual(origin, TAILSCALE_ORIGIN)
        self.assertEqual(parsed_offer_id, offer.offer_id)
        self.assertEqual(challenge.request_id, request.request_id)
        self.assertEqual(challenge.observed_server_id, SERVER_ID)
        self.assertTrue(
            hmac.compare_digest(
                base64url_decode(challenge.server_proof, expected_decoded_length=32),
                expected_server_proof,
            )
        )

        completion = self.completion(offer, challenge)
        receipt = self.service.complete_challenge(challenge.challenge_id, completion)
        replay = self.service.complete_challenge(challenge.challenge_id, completion)

        self.assertEqual(receipt, replay)
        self.assertEqual(receipt.state, "active_unassigned")
        self.assertEqual(receipt.observed_server_id, SERVER_ID)
        self.assertIn("no role", receipt.authorization_notice)
        devices = self.service.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].device_id, receipt.device_id)
        self.assertNotEqual(devices[0].auth_fingerprint, devices[0].approval_fingerprint)
        with self.connect() as connection:
            stored = connection.execute(
                "SELECT state, pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                (str(offer.offer_id),),
            ).fetchone()
            key_count = connection.execute("SELECT COUNT(*) FROM enrolled_device_keys").fetchone()[0]
        self.assertEqual(stored["state"], "completed")
        self.assertIsNone(stored["pairing_key"])
        self.assertEqual(key_count, 2)

    def test_wrong_proofs_lock_offer_after_five_generic_failures(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        challenge = self.service.create_challenge(self.challenge_request(offer))
        wrong = self.completion(
            offer,
            challenge,
            client_proof=base64url_encode(b"x" * 32),
        )

        for _ in range(5):
            with self.assertRaises(EnrollmentProtocolError) as raised:
                self.service.complete_challenge(challenge.challenge_id, wrong)
            self.assertEqual(raised.exception.code, "device_enrollment_failed")

        with self.connect() as connection:
            offer_state = connection.execute(
                "SELECT state, pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                (str(offer.offer_id),),
            ).fetchone()
            challenge_state = connection.execute(
                "SELECT state, failed_attempts FROM device_enrollment_challenges WHERE challenge_id = ?",
                (str(challenge.challenge_id),),
            ).fetchone()
        self.assertEqual(offer_state["state"], "locked")
        self.assertIsNone(offer_state["pairing_key"])
        self.assertEqual(challenge_state["state"], "locked")
        self.assertEqual(challenge_state["failed_attempts"], 5)

    def test_expiry_origin_and_distinct_key_boundaries_fail_closed(self):
        for origin in (
            "http://192.168.1.20:8080",
            "http://example.com",
            "https://example.com",
        ):
            with self.subTest(origin=origin), self.assertRaises(EnrollmentOriginError):
                self.service.issue_pairing_offer(origin)
        self.assertEqual(
            self.service.issue_pairing_offer("https://vps.tailnet-name.ts.net").audience_origin,
            "https://vps.tailnet-name.ts.net",
        )

        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        self.clock.advance(301)
        with self.assertRaises(EnrollmentProtocolError):
            self.service.create_challenge(self.challenge_request(offer))

        public_key = EnrollmentPublicKey(
            value=encode_p256_public_key(self.authentication_key.public_key())
        )
        with self.assertRaises(ValidationError):
            EnrollmentChallengeRequest(
                request_id=uuid4(),
                offer_id=uuid4(),
                observed_server_id=SERVER_ID,
                audience_origin=TAILSCALE_ORIGIN,
                client_nonce=base64url_encode(b"n" * 32),
                device=EnrollmentDeviceMetadata(
                    client_instance_id=uuid4(),
                    display_name="iPhone",
                    platform="ios",
                    os_version="17",
                    app_version="1",
                ),
                device_authentication_key=public_key,
                approval_key=public_key,
                claim_proof=base64url_encode(b"p" * 32),
            )

    def test_idempotency_rejects_changed_retry_and_concurrent_exact_retry_converges(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        challenge = self.service.create_challenge(self.challenge_request(offer))
        completion = self.completion(offer, challenge)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.service.complete_challenge, challenge.challenge_id, completion)
                for _ in range(2)
            ]
            receipts = [future.result() for future in futures]
        self.assertEqual(receipts[0], receipts[1])

        changed = completion.model_copy(
            update={"client_proof": base64url_encode(b"z" * 32)}
        )
        with self.assertRaises(EnrollmentConflictError):
            self.service.complete_challenge(challenge.challenge_id, changed)
        different_retry = completion.model_copy(update={"idempotency_key": uuid4()})
        with self.assertRaises(EnrollmentProtocolError):
            self.service.complete_challenge(challenge.challenge_id, different_retry)

    def test_high_s_signature_twin_is_the_same_idempotent_completion(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        challenge = self.service.create_challenge(self.challenge_request(offer))
        completion = self.completion(offer, challenge)
        signature = base64url_decode(completion.device_authentication_signature)
        r, s = decode_dss_signature(signature)
        twin = base64url_encode(encode_dss_signature(r, P256_ORDER - s))
        changed = completion.model_copy(
            update={"device_authentication_signature": twin}
        )

        first = self.service.complete_challenge(challenge.challenge_id, completion)
        replay = self.service.complete_challenge(challenge.challenge_id, changed)
        self.assertNotEqual(
            completion.device_authentication_signature,
            changed.device_authentication_signature,
        )
        self.assertEqual(replay, first)

    def test_revoked_device_cannot_replay_its_enrollment_receipt(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        challenge = self.service.create_challenge(self.challenge_request(offer))
        completion = self.completion(offer, challenge)
        receipt = self.service.complete_challenge(challenge.challenge_id, completion)
        self.assertTrue(self.service.revoke_device(receipt.device_id, "Lost phone"))
        with self.assertRaises(EnrollmentProtocolError):
            self.service.complete_challenge(challenge.challenge_id, completion)

    def test_swapped_key_signatures_fail_with_the_generic_protocol_error(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        challenge = self.service.create_challenge(self.challenge_request(offer))
        completion = self.completion(offer, challenge)
        swapped = completion.model_copy(
            update={
                "device_authentication_signature": completion.approval_signature,
                "approval_signature": completion.device_authentication_signature,
            }
        )
        with self.assertRaises(EnrollmentProtocolError) as raised:
            self.service.complete_challenge(challenge.challenge_id, swapped)
        self.assertEqual(raised.exception.code, "device_enrollment_failed")

    def test_expired_unclaimed_offer_is_scrubbed_without_another_enrollment_write(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        self.clock.advance(301)
        self.assertGreaterEqual(self.service.expire_pending(), 1)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state, pairing_key FROM device_pairing_offers WHERE offer_id = ?",
                (str(offer.offer_id),),
            ).fetchone()
        self.assertEqual(row["state"], "expired")
        self.assertIsNone(row["pairing_key"])

    def test_device_metadata_rejects_terminal_and_direction_controls(self):
        for display_name in (
            "Joe\x1b[2J",
            "Joe\u202ePhone",
            "Joe\u2028Phone",
            "Joe\ud800Phone",
        ):
            with self.subTest(display_name=repr(display_name)), self.assertRaises(
                ValidationError
            ):
                EnrollmentDeviceMetadata(
                    client_instance_id=uuid4(),
                    display_name=display_name,
                    platform="ios",
                    os_version="17",
                    app_version="1",
                )

    def test_local_revocation_disables_both_immutable_keys_and_audit_is_append_only(self):
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        challenge = self.service.create_challenge(self.challenge_request(offer))
        receipt = self.service.complete_challenge(
            challenge.challenge_id,
            self.completion(offer, challenge),
        )
        self.assertTrue(self.service.revoke_device(receipt.device_id, "iPhone was replaced"))
        self.assertFalse(self.service.revoke_device(receipt.device_id, "duplicate"))

        with self.connect() as connection:
            state = connection.execute(
                "SELECT state FROM enrolled_devices WHERE device_id = ?",
                (str(receipt.device_id),),
            ).fetchone()[0]
            active_keys = connection.execute(
                "SELECT COUNT(*) FROM enrolled_device_keys WHERE device_id = ? AND active = 1",
                (str(receipt.device_id),),
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM device_identity_events")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE enrolled_device_keys SET public_key = 'changed' WHERE device_id = ?",
                    (str(receipt.device_id),),
                )
        self.assertEqual(state, "revoked")
        self.assertEqual(active_keys, 0)


class DeviceEnrollmentRouterTests(DeviceEnrollmentFixture):
    def test_router_exposes_only_challenge_and_completion_not_offer_creation(self):
        app = FastAPI()
        app.state.device_enrollment_service = self.service
        app.include_router(router)
        offer = self.service.issue_pairing_offer(TAILSCALE_ORIGIN)
        request = self.challenge_request(offer)
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/device-enrollment/challenges",
                json=request.model_dump(mode="json"),
            )
            self.assertEqual(created.status_code, 201, created.text)
            challenge = created.json()
            completion = self.completion(
                offer,
                type("Challenge", (), {
                    "transcript_sha256": challenge["transcript_sha256"],
                    "device_authentication_payload": challenge["device_authentication_payload"],
                    "approval_payload": challenge["approval_payload"],
                })(),
            )
            completed = client.post(
                "/api/v1/device-enrollment/challenges/%s/complete" % challenge["challenge_id"],
                json=completion.model_dump(mode="json"),
            )
            no_remote_offer = client.post("/api/v1/device-enrollment/offers", json={})

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["state"], "active_unassigned")
        self.assertEqual(no_remote_offer.status_code, 404)
        self.assertNotIn("pairing_key", created.text)
        self.assertNotIn("manual_code", created.text)


class MainApplicationDeviceEnrollmentTests(DeviceEnrollmentFixture):
    def test_main_lifespan_wires_enrollment_without_remote_offer_creation(self):
        environment = {
            "JOEOS_DB_PATH": str(Path(self.tempdir.name) / "main-identity.db"),
            "LEMONADE_CONNECT_TIMEOUT": "0.1",
            "LEMONADE_READ_TIMEOUT": "0.2",
        }
        with patch.dict(os.environ, environment, clear=False):
            with TestClient(backend.app, base_url="http://127.0.0.1") as client:
                service = client.app.state.device_enrollment_service
                offer = service.issue_pairing_offer(TAILSCALE_ORIGIN)
                request = self.challenge_request(
                    offer,
                    observed_server_id=service.observed_server_id(),
                )
                created = client.post(
                    "/api/v1/device-enrollment/challenges",
                    json=request.model_dump(mode="json"),
                )
                self.assertEqual(created.status_code, 201, created.text)
                challenge = created.json()
                completion = self.completion(
                    offer,
                    type("Challenge", (), {
                        "transcript_sha256": challenge["transcript_sha256"],
                        "device_authentication_payload": challenge["device_authentication_payload"],
                        "approval_payload": challenge["approval_payload"],
                    })(),
                )
                completed = client.post(
                    "/api/v1/device-enrollment/challenges/%s/complete" % challenge["challenge_id"],
                    json=completion.model_dump(mode="json"),
                )
                remote_offer = client.post("/api/v1/device-enrollment/offers", json={})

        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["state"], "active_unassigned")
        self.assertEqual(remote_offer.status_code, 404)
        self.assertEqual(completed.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
