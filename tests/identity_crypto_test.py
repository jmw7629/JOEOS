import hashlib
import unittest
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import ValidationError

from server.identity.crypto import (
    GENERIC_PUBLIC_KEY_ERROR,
    GENERIC_SIGNATURE_ERROR,
    MAX_P256_SIGNATURE_ENCODED_LENGTH,
    MAX_P256_SPKI_ENCODED_LENGTH,
    MAX_SIGNED_BODY_LENGTH,
    Base64UrlEncodingError,
    PublicKeyValidationError,
    SignatureVerificationError,
    base64url_decode,
    base64url_encode,
    build_authenticated_request_signing_envelope,
    build_enrollment_signing_envelope,
    encode_p256_public_key,
    parse_p256_public_key,
    sha256_base64url,
    verify_p256_signature,
)
from server.identity.models import (
    AuthenticatedRequestSigningFields,
    EnrollmentSigningFields,
)


SERVER_ID = UUID("12345678-1234-4abc-8def-1234567890ab")
OTHER_SERVER_ID = UUID("22345678-1234-4abc-8def-1234567890ab")
DEVICE_ID = UUID("87654321-4321-4cba-8fed-ba0987654321")
OTHER_DEVICE_ID = UUID("97654321-4321-4cba-8fed-ba0987654321")
OFFER_ID = UUID("11111111-2222-4333-8444-555555555555")
OTHER_OFFER_ID = UUID("21111111-2222-4333-8444-555555555555")
REQUEST_ID = UUID("33333333-4444-4555-8666-777777777777")
OTHER_REQUEST_ID = UUID("43333333-4444-4555-8666-777777777777")
CHALLENGE_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
OTHER_CHALLENGE_ID = UUID("baaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
AUDIENCE_ORIGIN = "https://joeos.example.com"
NONCE = bytes(range(32))
TRANSCRIPT_SHA256 = hashlib.sha256(b"full pairing transcript").digest()
TIMESTAMP = 1785346200


def encoded_spki(public_key):
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64url_encode(der)


def encoded_signature(private_key, message):
    return base64url_encode(private_key.sign(message, ec.ECDSA(hashes.SHA256())))


class Base64UrlTests(unittest.TestCase):
    def test_round_trip_is_unpadded_and_canonical(self):
        encoded = base64url_encode(b"\x00\xffcanonical")

        self.assertNotIn("=", encoded)
        self.assertEqual(base64url_decode(encoded), b"\x00\xffcanonical")

    def test_padding_invalid_characters_noncanonical_bits_and_oversize_are_rejected(self):
        for value in ("AA==", "AA+", "AA/", "AB", "", "\u2603"):
            with self.subTest(value=value), self.assertRaises(Base64UrlEncodingError):
                base64url_decode(value, max_encoded_length=8)
        with self.assertRaises(Base64UrlEncodingError):
            base64url_decode("A" * 9, max_encoded_length=8)

    def test_expected_decoded_length_is_enforced(self):
        value = base64url_encode(b"short")

        with self.assertRaises(Base64UrlEncodingError):
            base64url_decode(value, expected_decoded_length=32)


class PublicKeyTests(unittest.TestCase):
    def setUp(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.spki = encode_p256_public_key(self.private_key.public_key())

    def assert_public_key_error(self, value):
        with self.assertRaises(PublicKeyValidationError) as raised:
            parse_p256_public_key(value)
        self.assertEqual(str(raised.exception), GENERIC_PUBLIC_KEY_ERROR)

    def test_p256_spki_is_canonicalized_and_fingerprinted_from_der(self):
        parsed = parse_p256_public_key(self.spki)

        self.assertEqual(parsed.canonical_spki, self.spki)
        self.assertEqual(parsed.canonical_der, base64url_decode(self.spki))
        self.assertEqual(
            parsed.fingerprint,
            base64url_encode(hashlib.sha256(parsed.canonical_der).digest()),
        )
        self.assertEqual(len(base64url_decode(parsed.fingerprint)), 32)

    def test_wrong_curve_and_non_ec_keys_are_rejected(self):
        p384 = ec.generate_private_key(ec.SECP384R1()).public_key()
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()

        self.assert_public_key_error(encoded_spki(p384))
        self.assert_public_key_error(encoded_spki(rsa_key))
        with self.assertRaises(PublicKeyValidationError):
            encode_p256_public_key(p384)
        with self.assertRaises(PublicKeyValidationError):
            encode_p256_public_key(rsa_key)

    def test_malformed_padded_trailing_and_oversized_spki_are_rejected(self):
        canonical_der = base64url_decode(self.spki)
        for value in (
            "not_der",
            self.spki + "=",
            base64url_encode(canonical_der + b"\x00"),
            "A" * (MAX_P256_SPKI_ENCODED_LENGTH + 1),
        ):
            with self.subTest(value=value[:20]):
                self.assert_public_key_error(value)


class SigningEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.spki = encode_p256_public_key(self.private_key.public_key())
        self.fingerprint = parse_p256_public_key(self.spki).fingerprint

    def test_enrollment_envelope_has_one_exact_ascii_representation(self):
        envelope = build_enrollment_signing_envelope(
            server_id=SERVER_ID,
            audience_origin=AUDIENCE_ORIGIN,
            offer_id=OFFER_ID,
            request_id=REQUEST_ID,
            challenge_id=CHALLENGE_ID,
            device_id=DEVICE_ID,
            key_purpose="device_authentication",
            key_fingerprint=self.fingerprint,
            transcript_sha256=TRANSCRIPT_SHA256,
            timestamp=TIMESTAMP,
            nonce=NONCE,
        )

        expected = (
            "JOEOS-DEVICE-ENROLLMENT-PROOF-V1:DEVICE-AUTHENTICATION\n"
            "server_id:12345678-1234-4abc-8def-1234567890ab\n"
            "audience_origin:https://joeos.example.com\n"
            "offer_id:11111111-2222-4333-8444-555555555555\n"
            "request_id:33333333-4444-4555-8666-777777777777\n"
            "challenge_id:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n"
            "device_id:87654321-4321-4cba-8fed-ba0987654321\n"
            "key_purpose:device_authentication\n"
            "key_fingerprint:" + self.fingerprint + "\n"
            "transcript_sha256:" + base64url_encode(TRANSCRIPT_SHA256) + "\n"
            "timestamp:1785346200\n"
            "nonce:" + base64url_encode(NONCE) + "\n"
        ).encode("ascii")
        self.assertEqual(envelope, expected)
        self.assertTrue(envelope.isascii())

    def test_each_enrollment_transcript_field_and_key_purpose_is_signature_bound(self):
        approval_key = ec.generate_private_key(ec.SECP256R1())
        approval_spki = encode_p256_public_key(approval_key.public_key())
        approval_fingerprint = parse_p256_public_key(approval_spki).fingerprint
        common = dict(
            server_id=SERVER_ID,
            audience_origin=AUDIENCE_ORIGIN,
            offer_id=OFFER_ID,
            request_id=REQUEST_ID,
            challenge_id=CHALLENGE_ID,
            device_id=DEVICE_ID,
            key_purpose="device_authentication",
            key_fingerprint=self.fingerprint,
            transcript_sha256=TRANSCRIPT_SHA256,
            timestamp=TIMESTAMP,
            nonce=NONCE,
        )
        envelope = build_enrollment_signing_envelope(**common)
        signature = encoded_signature(self.private_key, envelope)
        self.assertTrue(verify_p256_signature(self.spki, envelope, signature))

        mutations = (
            ("server_id", OTHER_SERVER_ID),
            ("audience_origin", "https://other.example.com"),
            ("offer_id", OTHER_OFFER_ID),
            ("request_id", OTHER_REQUEST_ID),
            ("challenge_id", OTHER_CHALLENGE_ID),
            ("device_id", OTHER_DEVICE_ID),
            ("key_purpose", "approval"),
            ("key_fingerprint", approval_fingerprint),
            ("transcript_sha256", hashlib.sha256(b"changed transcript").digest()),
            ("timestamp", TIMESTAMP + 1),
            ("nonce", bytes(reversed(NONCE))),
        )
        for field, value in mutations:
            changed = build_enrollment_signing_envelope(**{**common, field: value})
            with self.subTest(field=field), self.assertRaises(SignatureVerificationError):
                verify_p256_signature(self.spki, changed, signature)

        approval_envelope = build_enrollment_signing_envelope(
            **{
                **common,
                "key_purpose": "approval",
                "key_fingerprint": approval_fingerprint,
            }
        )
        approval_signature = encoded_signature(approval_key, approval_envelope)
        self.assertTrue(
            verify_p256_signature(approval_spki, approval_envelope, approval_signature)
        )
        self.assertNotEqual(envelope.splitlines()[0], approval_envelope.splitlines()[0])

    def test_enrollment_origin_and_digest_are_strictly_canonical_and_bounded(self):
        common = dict(
            server_id=SERVER_ID,
            audience_origin=AUDIENCE_ORIGIN,
            offer_id=OFFER_ID,
            request_id=REQUEST_ID,
            challenge_id=CHALLENGE_ID,
            device_id=DEVICE_ID,
            key_purpose="device_authentication",
            key_fingerprint=self.fingerprint,
            transcript_sha256=TRANSCRIPT_SHA256,
            timestamp=TIMESTAMP,
            nonce=NONCE,
        )
        for origin in (
            "HTTPS://joeos.example.com",
            "https://joeos.example.com/",
            "https://joeos.example.com?query=1",
            "https://user@joeos.example.com",
            "https://joeos.example.com:443",
            "https://[::ffff:c000:201]",
            "https://[::ffff:192.0.2.1]",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValidationError):
                build_enrollment_signing_envelope(**{**common, "audience_origin": origin})
        for digest in (TRANSCRIPT_SHA256[:-1], bytearray(TRANSCRIPT_SHA256), b"x" * 33):
            with self.subTest(digest_type=type(digest)), self.assertRaises(ValueError):
                build_enrollment_signing_envelope(**{**common, "transcript_sha256": digest})

    def test_request_envelope_hashes_the_exact_body_and_is_query_free(self):
        body = b'{"message": "hello"}\n'
        envelope = build_authenticated_request_signing_envelope(
            server_id=SERVER_ID,
            device_id=DEVICE_ID,
            key_fingerprint=self.fingerprint,
            method="POST",
            path="/api/v1/chat",
            body=body,
            timestamp=TIMESTAMP,
            nonce=NONCE,
        )

        expected = (
            "JOEOS-AUTHENTICATED-REQUEST-V1\n"
            "server_id:12345678-1234-4abc-8def-1234567890ab\n"
            "device_id:87654321-4321-4cba-8fed-ba0987654321\n"
            "key_fingerprint:" + self.fingerprint + "\n"
            "method:POST\n"
            "path:/api/v1/chat\n"
            "body_sha256:" + sha256_base64url(body) + "\n"
            "timestamp:1785346200\n"
            "nonce:" + base64url_encode(NONCE) + "\n"
        ).encode("ascii")
        self.assertEqual(envelope, expected)

        changed_whitespace = build_authenticated_request_signing_envelope(
            server_id=SERVER_ID,
            device_id=DEVICE_ID,
            key_fingerprint=self.fingerprint,
            method="POST",
            path="/api/v1/chat",
            body=b'{"message":"hello"}\n',
            timestamp=TIMESTAMP,
            nonce=NONCE,
        )
        self.assertNotEqual(envelope, changed_whitespace)

    def test_noncanonical_request_inputs_and_unbounded_values_are_rejected(self):
        common = dict(
            server_id=SERVER_ID,
            device_id=DEVICE_ID,
            key_fingerprint=self.fingerprint,
            method="POST",
            path="/api/v1/chat",
            body=b"{}",
            timestamp=TIMESTAMP,
            nonce=NONCE,
        )
        for name, value in (
            ("method", "post"),
            ("path", "/api/v1/chat?admin=true"),
            ("path", "//api/v1/chat"),
            ("path", "/api/v1/../chat"),
            ("nonce", NONCE[:-1]),
            ("timestamp", "1785346200"),
        ):
            arguments = dict(common)
            arguments[name] = value
            with self.subTest(name=name, value=value), self.assertRaises((ValidationError, ValueError)):
                build_authenticated_request_signing_envelope(**arguments)
        with self.assertRaises(ValueError):
            build_authenticated_request_signing_envelope(
                **{**common, "body": b"x" * (MAX_SIGNED_BODY_LENGTH + 1)}
            )

    def test_models_are_strict_frozen_and_reject_extra_fields(self):
        enrollment = EnrollmentSigningFields(
            server_id=SERVER_ID,
            audience_origin=AUDIENCE_ORIGIN,
            offer_id=OFFER_ID,
            request_id=REQUEST_ID,
            challenge_id=CHALLENGE_ID,
            device_id=DEVICE_ID,
            key_purpose="device_authentication",
            key_fingerprint=self.fingerprint,
            transcript_sha256=base64url_encode(TRANSCRIPT_SHA256),
            timestamp=TIMESTAMP,
            nonce=base64url_encode(NONCE),
        )
        with self.assertRaises(ValidationError):
            EnrollmentSigningFields(
                **enrollment.model_dump(),
                algorithm="ES256",
            )
        with self.assertRaises(ValidationError):
            EnrollmentSigningFields(
                **{**enrollment.model_dump(), "timestamp": str(TIMESTAMP)}
            )
        with self.assertRaises(ValidationError):
            enrollment.timestamp = TIMESTAMP + 1
        with self.assertRaises(ValidationError):
            AuthenticatedRequestSigningFields(
                **enrollment.model_dump(),
                method="POST",
                path="/api/v1/chat?query=1",
                body_sha256=sha256_base64url(b"{}"),
            )


class SignatureVerificationTests(unittest.TestCase):
    def setUp(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.spki = encode_p256_public_key(self.private_key.public_key())
        self.fingerprint = parse_p256_public_key(self.spki).fingerprint
        self.message = build_authenticated_request_signing_envelope(
            server_id=SERVER_ID,
            device_id=DEVICE_ID,
            key_fingerprint=self.fingerprint,
            method="POST",
            path="/api/v1/chat",
            body=b'{"message":"hello"}',
            timestamp=TIMESTAMP,
            nonce=NONCE,
        )
        self.signature = encoded_signature(self.private_key, self.message)

    def assert_signature_error(self, spki, message, signature):
        with self.assertRaises(SignatureVerificationError) as raised:
            verify_p256_signature(spki, message, signature)
        self.assertEqual(str(raised.exception), GENERIC_SIGNATURE_ERROR)

    def test_valid_der_ecdsa_sha256_signature_is_accepted(self):
        self.assertTrue(verify_p256_signature(self.spki, self.message, self.signature))

    def test_wrong_key_body_path_server_and_device_are_rejected_identically(self):
        other_key = ec.generate_private_key(ec.SECP256R1())
        wrong_key_spki = encode_p256_public_key(other_key.public_key())
        variants = [
            (wrong_key_spki, self.message),
            (
                self.spki,
                build_authenticated_request_signing_envelope(
                    server_id=SERVER_ID,
                    device_id=DEVICE_ID,
                    key_fingerprint=self.fingerprint,
                    method="POST",
                    path="/api/v1/chat",
                    body=b'{"message":"changed"}',
                    timestamp=TIMESTAMP,
                    nonce=NONCE,
                ),
            ),
            (
                self.spki,
                build_authenticated_request_signing_envelope(
                    server_id=SERVER_ID,
                    device_id=DEVICE_ID,
                    key_fingerprint=self.fingerprint,
                    method="POST",
                    path="/api/v1/bots",
                    body=b'{"message":"hello"}',
                    timestamp=TIMESTAMP,
                    nonce=NONCE,
                ),
            ),
            (
                self.spki,
                build_authenticated_request_signing_envelope(
                    server_id=OTHER_SERVER_ID,
                    device_id=DEVICE_ID,
                    key_fingerprint=self.fingerprint,
                    method="POST",
                    path="/api/v1/chat",
                    body=b'{"message":"hello"}',
                    timestamp=TIMESTAMP,
                    nonce=NONCE,
                ),
            ),
            (
                self.spki,
                build_authenticated_request_signing_envelope(
                    server_id=SERVER_ID,
                    device_id=OTHER_DEVICE_ID,
                    key_fingerprint=self.fingerprint,
                    method="POST",
                    path="/api/v1/chat",
                    body=b'{"message":"hello"}',
                    timestamp=TIMESTAMP,
                    nonce=NONCE,
                ),
            ),
        ]
        for spki, message in variants:
            with self.subTest(message=message[:80]):
                self.assert_signature_error(spki, message, self.signature)

    def test_malformed_noncanonical_oversized_and_modified_signatures_share_one_failure(self):
        signature_bytes = base64url_decode(self.signature)
        modified = bytearray(signature_bytes)
        modified[-1] ^= 1
        for signature in (
            "not_der",
            self.signature + "=",
            base64url_encode(bytes(modified)),
            "A" * (MAX_P256_SIGNATURE_ENCODED_LENGTH + 1),
        ):
            with self.subTest(signature=signature[:20]):
                self.assert_signature_error(self.spki, self.message, signature)

    def test_wrong_curve_public_key_and_empty_message_are_generic_signature_failures(self):
        p384_spki = encoded_spki(ec.generate_private_key(ec.SECP384R1()).public_key())

        self.assert_signature_error(p384_spki, self.message, self.signature)
        self.assert_signature_error(self.spki, b"", self.signature)


if __name__ == "__main__":
    unittest.main()
