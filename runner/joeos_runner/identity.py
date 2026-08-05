"""Runner identity: P-256 signing key generation, storage, and signing.

The private key never leaves the runner and is stored 0600 for the dedicated
runner user. The CLI never prints the private key.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from server.identity.crypto import base64url_encode

P256_ORDER = (
    0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
)


class RunnerIdentityError(Exception):
    pass


class RunnerSigner:
    def __init__(self, key_path: str, key_identifier: str) -> None:
        self._key_path = Path(key_path)
        self._key_identifier = key_identifier
        self._key: Optional[ec.EllipticCurvePrivateKey] = None

    def load(self) -> "RunnerSigner":
        if not self._key_path.is_file():
            raise RunnerIdentityError("runner key not found: %s" % self._key_path)
        mode = self._key_path.stat().st_mode & 0o777
        if mode & 0o077:
            raise RunnerIdentityError("runner key must be 0600: %s" % self._key_path)
        try:
            self._key = serialization.load_pem_private_key(
                self._key_path.read_bytes(), password=None
            )
        except Exception as error:  # noqa: BLE001
            raise RunnerIdentityError("runner key could not be loaded") from error
        if not isinstance(self._key, ec.EllipticCurvePrivateKey) or not isinstance(
            self._key.curve, ec.SECP256R1
        ):
            raise RunnerIdentityError("runner key must be a P-256 key")
        return self

    def public_key(self) -> str:
        key = self._require()
        der = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64url_encode(der)

    def key_identifier(self) -> str:
        return self._key_identifier

    def sign(self, message: str) -> str:
        key = self._require()
        signature = key.sign(message.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        return base64url_encode(_low_s(signature))

    def machine_fingerprint(self) -> str:
        import socket
        return hashlib.sha256(
            json.dumps({"host": socket.gethostname(), "key": self.public_key()},
                       sort_keys=True).encode()
        ).hexdigest()[:32]

    def _require(self) -> ec.EllipticCurvePrivateKey:
        if self._key is None:
            raise RunnerIdentityError("runner identity not loaded")
        return self._key


def initialize_identity(key_path: str, key_identifier: str) -> RunnerSigner:
    """Generates a P-256 runner key stored 0600. Never prints the private key."""
    path = Path(key_path)
    if path.exists():
        raise RunnerIdentityError("runner key already exists: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    return RunnerSigner(str(path), key_identifier).load()


def _low_s(signature: bytes) -> bytes:
    r, s = decode_dss_signature(signature)
    if s > P256_ORDER // 2:
        s = P256_ORDER - s
    return encode_dss_signature(r, s)
