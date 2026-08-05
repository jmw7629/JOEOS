import os
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from server.identity.crypto import base64url_encode
from server.identity.key_protection import (
    PROTECTED_KEY_BYTES,
    IdentityKeyConfigurationError,
    PairingKeyProtectionError,
    PairingKeyProtector,
    load_or_create_identity_master_key,
)


SERVER_ID = UUID("12345678-1234-4abc-8def-1234567890ab")
OFFER_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
ORIGIN = "http://100.98.25.26:8080"


class PairingKeyProtectorTests(unittest.TestCase):
    def test_round_trip_is_authenticated_and_does_not_persist_plaintext(self):
        pairing_key = b"p" * 32
        protector = PairingKeyProtector(
            b"m" * 32,
            nonce_source=lambda size: b"n" * size,
        )
        protected = protector.protect(
            pairing_key,
            server_id=SERVER_ID,
            offer_id=OFFER_ID,
            audience_origin=ORIGIN,
        )

        self.assertEqual(len(protected), PROTECTED_KEY_BYTES)
        self.assertNotIn(pairing_key, protected)
        self.assertEqual(
            protector.unprotect(
                protected,
                server_id=SERVER_ID,
                offer_id=OFFER_ID,
                audience_origin=ORIGIN,
            ),
            pairing_key,
        )

        for changed in (
            protected[:-1] + bytes([protected[-1] ^ 1]),
            protected,
        ):
            with self.subTest(changed_aad=changed is protected), self.assertRaises(
                PairingKeyProtectionError
            ):
                protector.unprotect(
                    changed,
                    server_id=SERVER_ID,
                    offer_id=UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
                    if changed is protected
                    else OFFER_ID,
                    audience_origin=ORIGIN,
                )

    def test_wrong_master_key_fails_with_the_same_public_error(self):
        protected = PairingKeyProtector(b"a" * 32).protect(
            b"p" * 32,
            server_id=SERVER_ID,
            offer_id=OFFER_ID,
            audience_origin=ORIGIN,
        )
        with self.assertRaises(PairingKeyProtectionError) as raised:
            PairingKeyProtector(b"b" * 32).unprotect(
                protected,
                server_id=SERVER_ID,
                offer_id=OFFER_ID,
                audience_origin=ORIGIN,
            )
        self.assertNotIn("tag", str(raised.exception).lower())


class IdentityMasterKeyTests(unittest.TestCase):
    def test_concurrent_creation_publishes_one_stable_key_without_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "joeos.db"
            workers = 12
            barrier = threading.Barrier(workers)

            def load(candidate_byte):
                barrier.wait(timeout=5)
                return load_or_create_identity_master_key(
                    database,
                    environment={},
                    random_bytes=lambda size: bytes([candidate_byte]) * size,
                )

            with ThreadPoolExecutor(max_workers=workers) as executor:
                keys = list(executor.map(load, range(1, workers + 1)))

            key_path = database.with_name("identity-master.key")
            self.assertTrue(all(key == keys[0] for key in keys))
            self.assertEqual(key_path.read_bytes(), keys[0])
            self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
            self.assertEqual(
                [
                    path.name
                    for path in Path(directory).iterdir()
                    if path.name.startswith(".joeos-identity-key-")
                ],
                [],
            )

    def test_owner_only_key_file_is_created_once_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "joeos.db"
            first = load_or_create_identity_master_key(
                database,
                environment={},
                random_bytes=lambda size: b"k" * size,
            )
            second = load_or_create_identity_master_key(
                database,
                environment={},
                random_bytes=lambda size: b"x" * size,
            )
            key_path = database.with_name("identity-master.key")

            self.assertEqual(first, b"k" * 32)
            self.assertEqual(second, first)
            self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)

    def test_environment_key_is_canonical_and_never_written(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "joeos.db"
            value = load_or_create_identity_master_key(
                database,
                environment={"JOEOS_IDENTITY_MASTER_KEY": base64url_encode(b"e" * 32)},
            )
            self.assertEqual(value, b"e" * 32)
            self.assertFalse(database.with_name("identity-master.key").exists())

            with self.assertRaises(IdentityKeyConfigurationError):
                load_or_create_identity_master_key(
                    database,
                    environment={"JOEOS_IDENTITY_MASTER_KEY": "not-valid="},
                )

    @unittest.skipIf(os.name == "nt", "POSIX permissions are not available on Windows.")
    def test_permissive_existing_key_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "joeos.db"
            key_path = database.with_name("identity-master.key")
            key_path.write_bytes(b"k" * 32)
            key_path.chmod(0o644)
            with self.assertRaises(IdentityKeyConfigurationError):
                load_or_create_identity_master_key(database, environment={})

    @unittest.skipIf(os.name == "nt", "Symbolic-link semantics differ on Windows.")
    def test_configured_symbolic_link_key_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "joeos.db"
            target = Path(directory) / "target.key"
            configured = Path(directory) / "configured.key"
            target.write_bytes(b"k" * 32)
            target.chmod(0o600)
            configured.symlink_to(target)
            with self.assertRaises(IdentityKeyConfigurationError):
                load_or_create_identity_master_key(
                    database,
                    environment={"JOEOS_IDENTITY_MASTER_KEY_FILE": str(configured)},
                )


if __name__ == "__main__":
    unittest.main()
