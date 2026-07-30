from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
import unicodedata
from uuid import UUID

from pydantic import Field, PrivateAttr, field_validator, model_validator

from .crypto import (
    MAX_P256_SIGNATURE_ENCODED_LENGTH,
    MAX_P256_SPKI_ENCODED_LENGTH,
    ParsedP256PublicKey,
    base64url_decode,
    parse_p256_public_key,
)
from .models import (
    BASE64URL_PATTERN,
    NONCE_BASE64URL_LENGTH,
    SHA256_BASE64URL_LENGTH,
    StrictIdentityModel,
    validate_canonical_audience_origin,
)


DevicePlatform = Literal["ios", "macos", "windows", "linux"]
EnrollmentState = Literal["active_unassigned"]


def _bounded_plain_text(value: str, field_name: str) -> str:
    if (
        value != value.strip()
        or any(
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in value
        )
    ):
        raise ValueError(field_name + " must be trimmed plain text.")
    return value


class EnrollmentPublicKey(StrictIdentityModel):
    _parsed: ParsedP256PublicKey = PrivateAttr()
    algorithm: Literal["ES256"] = "ES256"
    format: Literal["spki_der_base64url"] = "spki_der_base64url"
    value: str = Field(
        min_length=1,
        max_length=MAX_P256_SPKI_ENCODED_LENGTH,
        pattern=BASE64URL_PATTERN,
    )

    @model_validator(mode="after")
    def validate_public_key(self) -> "EnrollmentPublicKey":
        parsed = parse_p256_public_key(self.value)
        object.__setattr__(self, "_parsed", parsed)
        return self

    @property
    def parsed(self) -> ParsedP256PublicKey:
        return self._parsed


class EnrollmentDeviceMetadata(StrictIdentityModel):
    client_instance_id: UUID = Field(strict=False)
    display_name: str = Field(min_length=1, max_length=80)
    platform: DevicePlatform
    os_version: str = Field(min_length=1, max_length=40)
    app_version: str = Field(min_length=1, max_length=40)

    @field_validator("display_name", "os_version", "app_version")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _bounded_plain_text(value, info.field_name)


class EnrollmentChallengeRequest(StrictIdentityModel):
    schema_version: Literal[1] = 1
    request_id: UUID = Field(strict=False)
    offer_id: UUID = Field(strict=False)
    observed_server_id: UUID = Field(strict=False)
    audience_origin: str = Field(min_length=8, max_length=255)
    client_nonce: str = Field(
        min_length=NONCE_BASE64URL_LENGTH,
        max_length=NONCE_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )
    device: EnrollmentDeviceMetadata
    device_authentication_key: EnrollmentPublicKey
    approval_key: EnrollmentPublicKey
    claim_proof: str = Field(
        min_length=SHA256_BASE64URL_LENGTH,
        max_length=SHA256_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )

    @field_validator("audience_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return validate_canonical_audience_origin(value)

    @field_validator("client_nonce")
    @classmethod
    def validate_client_nonce(cls, value: str) -> str:
        base64url_decode(
            value,
            max_encoded_length=NONCE_BASE64URL_LENGTH,
            expected_decoded_length=32,
        )
        return value

    @field_validator("claim_proof")
    @classmethod
    def validate_claim_proof(cls, value: str) -> str:
        base64url_decode(
            value,
            max_encoded_length=SHA256_BASE64URL_LENGTH,
            expected_decoded_length=32,
        )
        return value

    @model_validator(mode="after")
    def validate_distinct_keys(self) -> "EnrollmentChallengeRequest":
        if (
            self.device_authentication_key.parsed.fingerprint
            == self.approval_key.parsed.fingerprint
        ):
            raise ValueError("Enrollment requires separate authentication and approval keys.")
        return self


class EnrollmentChallengeResponse(StrictIdentityModel):
    schema_version: Literal[1] = 1
    protocol: Literal["joeos-device-enrollment-v1"] = "joeos-device-enrollment-v1"
    request_id: UUID
    challenge_id: UUID
    offer_id: UUID
    device_id: UUID
    observed_server_id: UUID
    audience_origin: str
    issued_at: datetime
    expires_at: datetime
    server_nonce: str
    transcript_sha256: str
    server_proof: str
    device_authentication_payload: str
    approval_payload: str

    @model_validator(mode="after")
    def validate_response_bounds(self) -> "EnrollmentChallengeResponse":
        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() != timedelta(0):
            raise ValueError("issued_at must be timezone-aware UTC.")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() != timedelta(0):
            raise ValueError("expires_at must be timezone-aware UTC.")
        if self.expires_at <= self.issued_at:
            raise ValueError("Enrollment challenge expiry must follow issuance.")
        validate_canonical_audience_origin(self.audience_origin)
        for value in (self.server_nonce, self.transcript_sha256, self.server_proof):
            base64url_decode(
                value,
                max_encoded_length=SHA256_BASE64URL_LENGTH,
                expected_decoded_length=32,
            )
        for value in (self.device_authentication_payload, self.approval_payload):
            base64url_decode(value, max_encoded_length=4096)
        return self


class EnrollmentCompletionRequest(StrictIdentityModel):
    schema_version: Literal[1] = 1
    idempotency_key: UUID = Field(strict=False)
    transcript_sha256: str = Field(
        min_length=SHA256_BASE64URL_LENGTH,
        max_length=SHA256_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )
    client_proof: str = Field(
        min_length=SHA256_BASE64URL_LENGTH,
        max_length=SHA256_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )
    device_authentication_signature: str = Field(
        min_length=1,
        max_length=MAX_P256_SIGNATURE_ENCODED_LENGTH,
        pattern=BASE64URL_PATTERN,
    )
    approval_signature: str = Field(
        min_length=1,
        max_length=MAX_P256_SIGNATURE_ENCODED_LENGTH,
        pattern=BASE64URL_PATTERN,
    )

    @field_validator("transcript_sha256", "client_proof")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        base64url_decode(
            value,
            max_encoded_length=SHA256_BASE64URL_LENGTH,
            expected_decoded_length=32,
        )
        return value


class EnrollmentReceipt(StrictIdentityModel):
    schema_version: Literal[1] = 1
    enrollment_id: UUID
    device_id: UUID
    credential_id: str = Field(min_length=43, max_length=43, pattern=BASE64URL_PATTERN)
    observed_server_id: UUID
    audience_origin: str
    state: EnrollmentState = "active_unassigned"
    enrolled_at: datetime
    authentication_key_fingerprint: str
    approval_key_fingerprint: str
    authorization_notice: Literal[
        "Paired device has no role, session, approval, or execution authority."
    ] = "Paired device has no role, session, approval, or execution authority."

    @model_validator(mode="after")
    def validate_receipt(self) -> "EnrollmentReceipt":
        validate_canonical_audience_origin(self.audience_origin)
        if self.enrolled_at.tzinfo is None or self.enrolled_at.utcoffset() != timedelta(0):
            raise ValueError("enrolled_at must be timezone-aware UTC.")
        for value in (
            self.credential_id,
            self.authentication_key_fingerprint,
            self.approval_key_fingerprint,
        ):
            base64url_decode(value, max_encoded_length=43, expected_decoded_length=32)
        return self
