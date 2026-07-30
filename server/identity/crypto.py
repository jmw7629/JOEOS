from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID


class CryptographyConfigurationError(RuntimeError):
    """Raised when the required cryptographic provider is not installed."""


try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature,
        encode_dss_signature,
    )
except ImportError as exc:  # pragma: no cover - exercised by deployment startup.
    raise CryptographyConfigurationError(
        "JoeOS device identity requires the 'cryptography' package."
    ) from exc


BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_BASE64URL_ENCODED_LENGTH = 4096
MAX_P256_SPKI_ENCODED_LENGTH = 256
MAX_P256_SPKI_DER_LENGTH = 192
MAX_P256_SIGNATURE_ENCODED_LENGTH = 128
MAX_P256_SIGNATURE_DER_LENGTH = 80
MAX_SIGNED_MESSAGE_LENGTH = 4096
MAX_SIGNED_BODY_LENGTH = 1024 * 1024
SHA256_LENGTH = 32
NONCE_LENGTH = 32
P256_ORDER = int(
    "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551",
    16,
)
ENROLLMENT_DOMAIN = "JOEOS-DEVICE-ENROLLMENT-PROOF-V1"
REQUEST_DOMAIN = "JOEOS-AUTHENTICATED-REQUEST-V1"
ENROLLMENT_PURPOSE_DOMAINS: Dict[str, str] = {
    "device_authentication": ENROLLMENT_DOMAIN + ":DEVICE-AUTHENTICATION",
    "approval": ENROLLMENT_DOMAIN + ":APPROVAL",
}
GENERIC_PUBLIC_KEY_ERROR = "Invalid P-256 public key."
GENERIC_SIGNATURE_ERROR = "Device signature verification failed."


class Base64UrlEncodingError(ValueError):
    """Raised when a value is not canonical unpadded base64url."""


class PublicKeyValidationError(ValueError):
    """Raised without exposing why an untrusted public key was rejected."""


class SignatureVerificationError(ValueError):
    """The single public failure for device-signature verification."""


@dataclass(frozen=True)
class ParsedP256PublicKey:
    public_key: ec.EllipticCurvePublicKey
    canonical_der: bytes
    canonical_spki: str
    fingerprint: str


def base64url_encode(value: bytes) -> str:
    """Encode bytes as canonical RFC 4648 base64url without padding."""

    if type(value) is not bytes:
        raise TypeError("base64url encoding requires bytes.")
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(
    value: str,
    *,
    max_encoded_length: int = MAX_BASE64URL_ENCODED_LENGTH,
    expected_decoded_length: Optional[int] = None,
) -> bytes:
    """Decode only bounded, canonical, unpadded RFC 4648 base64url."""

    if type(value) is not str or not value:
        raise Base64UrlEncodingError("Invalid base64url value.")
    if type(max_encoded_length) is not int or max_encoded_length < 1:
        raise ValueError("max_encoded_length must be a positive integer.")
    if len(value) > max_encoded_length:
        raise Base64UrlEncodingError("Invalid base64url value.")
    if "=" in value or len(value) % 4 == 1 or BASE64URL_PATTERN.fullmatch(value) is None:
        raise Base64UrlEncodingError("Invalid base64url value.")
    try:
        ascii_value = value.encode("ascii")
        padding = b"=" * ((4 - len(ascii_value) % 4) % 4)
        decoded = base64.b64decode(
            ascii_value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise Base64UrlEncodingError("Invalid base64url value.") from exc
    if expected_decoded_length is not None:
        if type(expected_decoded_length) is not int or expected_decoded_length < 0:
            raise ValueError("expected_decoded_length must be a nonnegative integer.")
        if len(decoded) != expected_decoded_length:
            raise Base64UrlEncodingError("Invalid base64url value.")
    if not hmac.compare_digest(base64url_encode(decoded), value):
        raise Base64UrlEncodingError("Invalid base64url value.")
    return decoded


def sha256_base64url(value: bytes) -> str:
    if type(value) is not bytes:
        raise TypeError("SHA-256 input must be bytes.")
    return base64url_encode(hashlib.sha256(value).digest())


def parse_p256_public_key(encoded_spki: str) -> ParsedP256PublicKey:
    """Parse one canonical DER SubjectPublicKeyInfo containing a P-256 key."""

    try:
        der = base64url_decode(
            encoded_spki,
            max_encoded_length=MAX_P256_SPKI_ENCODED_LENGTH,
        )
        if len(der) > MAX_P256_SPKI_DER_LENGTH:
            raise ValueError
        public_key = serialization.load_der_public_key(der)
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValueError
        if not isinstance(public_key.curve, ec.SECP256R1):
            raise ValueError
        canonical_der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        canonical_spki = base64url_encode(canonical_der)
        if not hmac.compare_digest(der, canonical_der):
            raise ValueError
        if not hmac.compare_digest(encoded_spki, canonical_spki):
            raise ValueError
    except Exception:
        raise PublicKeyValidationError(GENERIC_PUBLIC_KEY_ERROR) from None
    return ParsedP256PublicKey(
        public_key=public_key,
        canonical_der=canonical_der,
        canonical_spki=canonical_spki,
        fingerprint=sha256_base64url(canonical_der),
    )


def encode_p256_public_key(public_key: Any) -> str:
    """Serialize a P-256 public key to the only accepted SPKI representation."""

    try:
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValueError
        if not isinstance(public_key.curve, ec.SECP256R1):
            raise ValueError
        der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        encoded = base64url_encode(der)
        return parse_p256_public_key(encoded).canonical_spki
    except Exception:
        raise PublicKeyValidationError(GENERIC_PUBLIC_KEY_ERROR) from None


def _decode_der_signature(encoded_signature: str) -> bytes:
    signature = base64url_decode(
        encoded_signature,
        max_encoded_length=MAX_P256_SIGNATURE_ENCODED_LENGTH,
    )
    if len(signature) > MAX_P256_SIGNATURE_DER_LENGTH:
        raise ValueError
    r, s = decode_dss_signature(signature)
    if not (1 <= r < P256_ORDER and 1 <= s < P256_ORDER):
        raise ValueError
    if not hmac.compare_digest(signature, encode_dss_signature(r, s)):
        raise ValueError
    return signature


def verify_p256_signature(
    encoded_spki: str,
    message: bytes,
    encoded_signature: str,
) -> bool:
    """Verify canonical DER ECDSA/SHA-256 or raise one generic failure."""

    try:
        if type(message) is not bytes or not message or len(message) > MAX_SIGNED_MESSAGE_LENGTH:
            raise ValueError
        parsed = parse_p256_public_key(encoded_spki)
        signature = _decode_der_signature(encoded_signature)
        parsed.public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except CryptographyConfigurationError:
        raise
    except Exception:
        raise SignatureVerificationError(GENERIC_SIGNATURE_ERROR) from None
    return True


def canonicalize_p256_signature(encoded_signature: str) -> str:
    """Return the unique low-S encoding used for semantic request digests."""

    try:
        signature = _decode_der_signature(encoded_signature)
        r, s = decode_dss_signature(signature)
        if s > P256_ORDER // 2:
            s = P256_ORDER - s
        return base64url_encode(encode_dss_signature(r, s))
    except Exception:
        raise SignatureVerificationError(GENERIC_SIGNATURE_ERROR) from None


def _nonce_base64url(nonce: bytes) -> str:
    if type(nonce) is not bytes or len(nonce) != NONCE_LENGTH:
        raise ValueError("Device signing nonces must contain exactly 32 bytes.")
    return base64url_encode(nonce)


def _sha256_digest_base64url(digest: bytes) -> str:
    if type(digest) is not bytes or len(digest) != SHA256_LENGTH:
        raise ValueError("Pairing transcript digests must contain exactly 32 bytes.")
    return base64url_encode(digest)


def _validate_body(body: bytes) -> None:
    if type(body) is not bytes or len(body) > MAX_SIGNED_BODY_LENGTH:
        raise ValueError("Authenticated request bodies must be bounded bytes.")


def build_enrollment_signing_envelope(
    *,
    server_id: UUID,
    audience_origin: str,
    offer_id: UUID,
    request_id: UUID,
    challenge_id: UUID,
    device_id: UUID,
    key_purpose: str,
    key_fingerprint: str,
    transcript_sha256: bytes,
    timestamp: int,
    nonce: bytes,
) -> bytes:
    """Build the exact proof-of-possession bytes for device enrollment."""

    from server.identity.models import EnrollmentSigningFields

    fields = EnrollmentSigningFields(
        server_id=server_id,
        audience_origin=audience_origin,
        offer_id=offer_id,
        request_id=request_id,
        challenge_id=challenge_id,
        device_id=device_id,
        key_purpose=key_purpose,
        key_fingerprint=key_fingerprint,
        transcript_sha256=_sha256_digest_base64url(transcript_sha256),
        timestamp=timestamp,
        nonce=_nonce_base64url(nonce),
    )
    value = "\n".join(
        (
            ENROLLMENT_PURPOSE_DOMAINS[fields.key_purpose],
            "server_id:" + str(fields.server_id),
            "audience_origin:" + fields.audience_origin,
            "offer_id:" + str(fields.offer_id),
            "request_id:" + str(fields.request_id),
            "challenge_id:" + str(fields.challenge_id),
            "device_id:" + str(fields.device_id),
            "key_purpose:" + fields.key_purpose,
            "key_fingerprint:" + fields.key_fingerprint,
            "transcript_sha256:" + fields.transcript_sha256,
            "timestamp:" + str(fields.timestamp),
            "nonce:" + fields.nonce,
            "",
        )
    )
    return value.encode("ascii")


def build_authenticated_request_signing_envelope(
    *,
    server_id: UUID,
    device_id: UUID,
    key_fingerprint: str,
    method: str,
    path: str,
    body: bytes,
    timestamp: int,
    nonce: bytes,
) -> bytes:
    """Bind a query-free HTTP request and its exact raw body to a device key."""

    from server.identity.models import AuthenticatedRequestSigningFields

    _validate_body(body)
    fields = AuthenticatedRequestSigningFields(
        server_id=server_id,
        device_id=device_id,
        key_fingerprint=key_fingerprint,
        method=method,
        path=path,
        body_sha256=sha256_base64url(body),
        timestamp=timestamp,
        nonce=_nonce_base64url(nonce),
    )
    value = "\n".join(
        (
            REQUEST_DOMAIN,
            "server_id:" + str(fields.server_id),
            "device_id:" + str(fields.device_id),
            "key_fingerprint:" + fields.key_fingerprint,
            "method:" + fields.method,
            "path:" + fields.path,
            "body_sha256:" + fields.body_sha256,
            "timestamp:" + str(fields.timestamp),
            "nonce:" + fields.nonce,
            "",
        )
    )
    return value.encode("ascii")
