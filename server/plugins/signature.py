"""Signature architecture for the JoeOS Plugin Platform.

Plugins may carry an ECDSA P-256 signature over the package inventory root
hash (the same root hash recorded by the Integrity Service). Signature state
is computed deterministically and honestly:

* ``valid_first_party`` - signed by a configured first-party key.
* ``valid_user_trusted``  - signed by a key the user explicitly trusted.
* ``unsigned``            - no signature; policy decides whether this is allowed.
* ``invalid`` / ``locally_modified`` / ``expired`` / ``revoked`` - rejected.

This implementation validates signatures with the platform ``cryptography``
primitives (no custom crypto, no hard-coded signing keys). It does NOT ship a
public marketplace or a distribution of private keys; the initial release
supports validation architecture plus a clearly labeled development-mode
exception. Publishing and marketplace signing remain future work.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .models import SignatureState

SIGNING_DOMAIN = "JOEOS-PLUGIN-SIGNATURE-V1"
MAX_SPKI_LENGTH = 512
MAX_SIGNATURE_LENGTH = 512
PUBLIC_KEY_PATTERN = (
    "ecdsa-p256"  # accepted public key format for this release
)


class SignatureError(RuntimeError):
    pass


def _public_key_fingerprint(public_key: ec.EllipticCurvePublicKey) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _load_public_key(encoded_spki: str) -> ec.EllipticCurvePublicKey:
    if type(encoded_spki) is not str or len(encoded_spki) > MAX_SPKI_LENGTH:
        raise SignatureError("Invalid public key encoding.")
    try:
        der = base64.urlsafe_b64decode(encoded_spki + "=" * (-len(encoded_spki) % 4))
        public_key = serialization.load_der_public_key(der)
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise SignatureError("Signature public key is not an EC key.")
        if not isinstance(public_key.curve, ec.SECP256R1):
            raise SignatureError("Signature public key is not P-256.")
        return public_key
    except (ValueError, TypeError, base64.binascii.Error):
        raise SignatureError("Invalid public key encoding.") from None


def signing_envelope(inventory_root_hash: str, plugin_id: str, version: str) -> bytes:
    """Build the canonical bytes a publisher signs."""
    return (
        SIGNING_DOMAIN + "\n"
        + "plugin_id:" + plugin_id + "\n"
        + "version:" + version + "\n"
        + "inventory_root_hash:" + inventory_root_hash + "\n"
    ).encode("utf-8")


def sign_inventory(
    inventory_root_hash: str,
    plugin_id: str,
    version: str,
    private_key: ec.EllipticCurvePrivateKey,
) -> str:
    """Sign a package inventory with a P-256 private key (CLI/tooling use)."""
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise SignatureError("Signing requires an EC private key.")
    envelope = signing_envelope(inventory_root_hash, plugin_id, version)
    signature = private_key.sign(envelope, ec.ECDSA(hashes.SHA256()))
    return base64.urlsafe_b64encode(signature).decode("ascii")


def verify_inventory_signature(
    inventory_root_hash: str,
    plugin_id: str,
    version: str,
    encoded_signature: str,
    public_key: ec.EllipticCurvePublicKey,
) -> bool:
    """Verify a package signature; returns False for any invalid signature."""
    try:
        if type(encoded_signature) is not str or len(encoded_signature) > MAX_SIGNATURE_LENGTH:
            return False
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        envelope = signing_envelope(inventory_root_hash, plugin_id, version)
        public_key.verify(signature, envelope, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class SignatureEvaluation:
    state: SignatureState
    signer_fingerprint: str = ""


def evaluate_signature(
    *,
    inventory_root_hash: str,
    plugin_id: str,
    version: str,
    encoded_signature: str,
    signer_public_key: str = "",
    trusted_fingerprints: Tuple[str, ...] = (),
    first_party_fingerprints: Tuple[str, ...] = (),
    local_modified: bool = False,
) -> SignatureEvaluation:
    """Compute the honest signature state for an installed package.

    ``local_modified`` reflects a live integrity check that disagrees with the
    recorded inventory; a modified package is never treated as validly signed.
    ``signer_public_key`` is the publisher's P-256 SPKI that the signature must
    verify against; fingerprints are derived from it.
    """
    if local_modified:
        return SignatureEvaluation("locally_modified")
    if not encoded_signature:
        return SignatureEvaluation("unsigned")
    if not signer_public_key:
        return SignatureEvaluation("unavailable")
    try:
        public_key = _load_public_key(signer_public_key)
    except SignatureError:
        return SignatureEvaluation("invalid")
    fingerprint = _public_key_fingerprint(public_key)
    if not verify_inventory_signature(
        inventory_root_hash, plugin_id, version, encoded_signature, public_key
    ):
        return SignatureEvaluation("invalid", fingerprint)
    if fingerprint in first_party_fingerprints:
        return SignatureEvaluation("valid_first_party", fingerprint)
    if fingerprint in trusted_fingerprints:
        return SignatureEvaluation("valid_user_trusted", fingerprint)
    return SignatureEvaluation("valid", fingerprint)


def generate_development_key_pair() -> Tuple[ec.EllipticCurvePrivateKey, str]:
    """Generate a P-256 key pair for local development-mode signing.

    The private key is returned to the caller (local development tooling) and
    never persisted by the platform. Development keys are not treated as
    production trust.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    encoded = base64.urlsafe_b64encode(der).decode("ascii").rstrip("=")
    return private_key, encoded


def load_public_key_from_spki(encoded_spki: str) -> ec.EllipticCurvePublicKey:
    return _load_public_key(encoded_spki)


def public_key_fingerprint(public_key: ec.EllipticCurvePublicKey) -> str:
    return _public_key_fingerprint(public_key)