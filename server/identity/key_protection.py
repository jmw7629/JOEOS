from __future__ import annotations

import os
import secrets
import stat
import hashlib
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Optional
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import Base64UrlEncodingError, base64url_decode
from .crypto import base64url_encode


MASTER_KEY_BYTES = 32
PAIRING_KEY_BYTES = 32
NONCE_BYTES = 12
PROTECTED_KEY_PREFIX = b"JPK1"
PROTECTED_KEY_BYTES = len(PROTECTED_KEY_PREFIX) + NONCE_BYTES + PAIRING_KEY_BYTES + 16
MASTER_KEY_ENVIRONMENT_VARIABLE = "JOEOS_IDENTITY_MASTER_KEY"
MASTER_KEY_FILE_ENVIRONMENT_VARIABLE = "JOEOS_IDENTITY_MASTER_KEY_FILE"


class IdentityKeyConfigurationError(RuntimeError):
    """Raised when the local identity master key cannot be loaded safely."""


class PairingKeyProtectionError(RuntimeError):
    """One generic failure for corrupt, swapped, or incorrectly keyed ciphertext."""


class PairingKeyProtector:
    """AES-256-GCM protection for the short-lived pairing key persisted by SQLite."""

    def __init__(
        self,
        master_key: bytes,
        nonce_source: Optional[Callable[[int], bytes]] = None,
    ) -> None:
        if type(master_key) is not bytes or len(master_key) != MASTER_KEY_BYTES:
            raise IdentityKeyConfigurationError(
                "The JoeOS identity master key must contain exactly 32 bytes."
            )
        self._cipher = AESGCM(master_key)
        self._nonce_source = nonce_source or secrets.token_bytes
        self._identifier = base64url_encode(
            hashlib.sha256(b"JOEOS-IDENTITY-MASTER-KEY-ID-V1\0" + master_key).digest()
        )

    @property
    def identifier(self) -> str:
        """Return a non-secret identifier used to reject mixed runtime configuration."""

        return self._identifier

    def protect(
        self,
        pairing_key: bytes,
        *,
        server_id: UUID,
        offer_id: UUID,
        audience_origin: str,
    ) -> bytes:
        if type(pairing_key) is not bytes or len(pairing_key) != PAIRING_KEY_BYTES:
            raise ValueError("Pairing keys must contain exactly 32 bytes.")
        nonce = self._nonce_source(NONCE_BYTES)
        if type(nonce) is not bytes or len(nonce) != NONCE_BYTES:
            raise TypeError("Pairing-key nonce provider returned an invalid byte sequence.")
        ciphertext = self._cipher.encrypt(
            nonce,
            pairing_key,
            self._associated_data(server_id, offer_id, audience_origin),
        )
        return PROTECTED_KEY_PREFIX + nonce + ciphertext

    def unprotect(
        self,
        protected_key: bytes,
        *,
        server_id: UUID,
        offer_id: UUID,
        audience_origin: str,
    ) -> bytes:
        try:
            if type(protected_key) is not bytes or len(protected_key) != PROTECTED_KEY_BYTES:
                raise ValueError
            if protected_key[: len(PROTECTED_KEY_PREFIX)] != PROTECTED_KEY_PREFIX:
                raise ValueError
            offset = len(PROTECTED_KEY_PREFIX)
            nonce = protected_key[offset : offset + NONCE_BYTES]
            ciphertext = protected_key[offset + NONCE_BYTES :]
            pairing_key = self._cipher.decrypt(
                nonce,
                ciphertext,
                self._associated_data(server_id, offer_id, audience_origin),
            )
            if len(pairing_key) != PAIRING_KEY_BYTES:
                raise ValueError
            return pairing_key
        except (InvalidTag, TypeError, ValueError):
            raise PairingKeyProtectionError(
                "Stored device-pairing material could not be authenticated."
            ) from None

    @staticmethod
    def _associated_data(server_id: UUID, offer_id: UUID, audience_origin: str) -> bytes:
        if not isinstance(server_id, UUID) or not isinstance(offer_id, UUID):
            raise TypeError("Pairing-key identity fields must be UUID instances.")
        if type(audience_origin) is not str or not audience_origin:
            raise TypeError("Pairing-key audience must be a nonempty origin string.")
        return (
            b"JOEOS-PAIRING-KEY-AT-REST-V1\0"
            + server_id.bytes
            + offer_id.bytes
            + len(audience_origin.encode("ascii")).to_bytes(2, "big")
            + audience_origin.encode("ascii")
        )


def load_or_create_identity_master_key(
    database_path: Path,
    *,
    environment: Optional[Mapping[str, str]] = None,
    random_bytes: Optional[Callable[[int], bytes]] = None,
) -> bytes:
    """Load an injected key or atomically create a separate owner-only local key file."""

    values = environment if environment is not None else os.environ
    encoded = values.get(MASTER_KEY_ENVIRONMENT_VARIABLE, "").strip()
    if encoded:
        try:
            return base64url_decode(
                encoded,
                max_encoded_length=43,
                expected_decoded_length=MASTER_KEY_BYTES,
            )
        except Base64UrlEncodingError:
            raise IdentityKeyConfigurationError(
                "JOEOS_IDENTITY_MASTER_KEY must be canonical unpadded base64url for 32 bytes."
            ) from None

    configured_path = values.get(MASTER_KEY_FILE_ENVIRONMENT_VARIABLE, "").strip()
    path = (
        Path(configured_path).expanduser().absolute()
        if configured_path
        else Path(database_path).expanduser().absolute().with_name("identity-master.key")
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    generator = random_bytes or secrets.token_bytes
    candidate = generator(MASTER_KEY_BYTES)
    if type(candidate) is not bytes or len(candidate) != MASTER_KEY_BYTES:
        raise IdentityKeyConfigurationError(
            "The identity master-key generator returned an invalid byte sequence."
        )

    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".joeos-identity-key-",
            dir=str(path.parent),
        )
        os.fchmod(descriptor, 0o600)
    except OSError as error:
        raise IdentityKeyConfigurationError(
            "JoeOS could not create an owner-only temporary identity key."
        ) from error

    try:
        try:
            view = memoryview(candidate)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise IdentityKeyConfigurationError(
                        "JoeOS could not write its complete identity master key."
                    )
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary_name, str(path), follow_symlinks=False)
        except FileExistsError:
            return _read_identity_master_key(path)
        except (NotImplementedError, TypeError):
            raise IdentityKeyConfigurationError(
                "This platform cannot atomically publish the JoeOS identity key."
            ) from None
        except OSError as error:
            raise IdentityKeyConfigurationError(
                "JoeOS could not atomically publish its identity master key."
            ) from error
        try:
            directory_descriptor = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
        return candidate
    finally:
        try:
            Path(temporary_name).unlink()
        except OSError:
            pass


def _read_identity_master_key(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise IdentityKeyConfigurationError(
                "The JoeOS identity master-key path must be a regular file."
            )
        if metadata.st_mode & 0o077:
            raise IdentityKeyConfigurationError(
                "The JoeOS identity master-key file must be readable only by its owner (mode 0600)."
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(path), flags)
        try:
            value = os.read(descriptor, MASTER_KEY_BYTES + 1)
        finally:
            os.close(descriptor)
    except IdentityKeyConfigurationError:
        raise
    except OSError as error:
        raise IdentityKeyConfigurationError(
            "JoeOS could not read its identity master-key file."
        ) from error
    if len(value) != MASTER_KEY_BYTES:
        raise IdentityKeyConfigurationError(
            "The JoeOS identity master-key file must contain exactly 32 bytes."
        )
    return value
