from __future__ import annotations

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


SHA256_BASE64URL_LENGTH = 43
NONCE_BASE64URL_LENGTH = 43
BASE64URL_PATTERN = r"^[A-Za-z0-9_-]+$"
CANONICAL_API_PATH_PATTERN = re.compile(r"^/api/[A-Za-z0-9._~/-]*$")
MAX_API_PATH_LENGTH = 512
MAX_AUDIENCE_ORIGIN_LENGTH = 255
MAX_TIMESTAMP = (1 << 63) - 1
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
KeyPurpose = Literal["device_authentication", "approval"]
HOSTNAME_PATTERN = re.compile(
    r"^(?:localhost|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*)$"
)


class StrictIdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_sha256_base64url(value: str, field_name: str) -> str:
    from server.identity.crypto import SHA256_LENGTH, base64url_decode

    if len(value) != SHA256_BASE64URL_LENGTH:
        raise ValueError(field_name + " must encode exactly 32 bytes.")
    base64url_decode(
        value,
        max_encoded_length=SHA256_BASE64URL_LENGTH,
        expected_decoded_length=SHA256_LENGTH,
    )
    return value


def validate_canonical_audience_origin(value: str) -> str:
    try:
        value.encode("ascii")
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeEncodeError, ValueError):
        raise ValueError("audience_origin must be a canonical ASCII HTTP origin.") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        raise ValueError("audience_origin must be a canonical ASCII HTTP origin.")
    host = parsed.hostname
    if "%" in host or host.endswith("."):
        raise ValueError("audience_origin must be a canonical ASCII HTTP origin.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if HOSTNAME_PATTERN.fullmatch(host) is None:
            raise ValueError("audience_origin must be a canonical ASCII HTTP origin.") from None
        canonical_host = host
    else:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            raise ValueError(
                "audience_origin does not permit IPv4-mapped IPv6 addresses."
            )
        canonical_host = "[" + address.compressed + "]" if address.version == 6 else str(address)
    if port in {80, 443} and (
        (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    ):
        raise ValueError("audience_origin must omit default ports.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("audience_origin must use a valid TCP port.")
    canonical = parsed.scheme + "://" + canonical_host
    if port is not None:
        canonical += ":" + str(port)
    if value != canonical:
        raise ValueError("audience_origin must be a canonical ASCII HTTP origin.")
    return value


class DeviceSigningFields(StrictIdentityModel):
    schema_version: Literal[1] = 1
    server_id: UUID
    device_id: UUID
    key_fingerprint: str = Field(
        min_length=SHA256_BASE64URL_LENGTH,
        max_length=SHA256_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )
    timestamp: int = Field(ge=0, le=MAX_TIMESTAMP)
    nonce: str = Field(
        min_length=NONCE_BASE64URL_LENGTH,
        max_length=NONCE_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )

    @field_validator("key_fingerprint")
    @classmethod
    def validate_key_fingerprint(cls, value: str) -> str:
        return _validate_sha256_base64url(value, "key_fingerprint")

    @field_validator("nonce")
    @classmethod
    def validate_nonce(cls, value: str) -> str:
        return _validate_sha256_base64url(value, "nonce")


class EnrollmentSigningFields(DeviceSigningFields):
    audience_origin: str = Field(min_length=8, max_length=MAX_AUDIENCE_ORIGIN_LENGTH)
    offer_id: UUID
    request_id: UUID
    challenge_id: UUID
    key_purpose: KeyPurpose
    transcript_sha256: str = Field(
        min_length=SHA256_BASE64URL_LENGTH,
        max_length=SHA256_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )

    @field_validator("audience_origin")
    @classmethod
    def validate_audience_origin(cls, value: str) -> str:
        return validate_canonical_audience_origin(value)

    @field_validator("transcript_sha256")
    @classmethod
    def validate_transcript_sha256(cls, value: str) -> str:
        return _validate_sha256_base64url(value, "transcript_sha256")


class AuthenticatedRequestSigningFields(DeviceSigningFields):
    method: HttpMethod
    path: str = Field(min_length=6, max_length=MAX_API_PATH_LENGTH)
    body_sha256: str = Field(
        min_length=SHA256_BASE64URL_LENGTH,
        max_length=SHA256_BASE64URL_LENGTH,
        pattern=BASE64URL_PATTERN,
    )

    @field_validator("path")
    @classmethod
    def validate_canonical_query_free_path(cls, value: str) -> str:
        if (
            CANONICAL_API_PATH_PATTERN.fullmatch(value) is None
            or "//" in value
            or any(segment in {".", ".."} for segment in value.split("/"))
        ):
            raise ValueError("path must be a canonical query-free /api/ path.")
        return value

    @field_validator("body_sha256")
    @classmethod
    def validate_body_sha256(cls, value: str) -> str:
        return _validate_sha256_base64url(value, "body_sha256")
