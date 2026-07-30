from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Optional
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .crypto import (
    SignatureVerificationError,
    base64url_decode,
    base64url_encode,
    build_enrollment_signing_envelope,
    canonicalize_p256_signature,
    verify_p256_signature,
)
from .enrollment_models import (
    EnrollmentChallengeRequest,
    EnrollmentChallengeResponse,
    EnrollmentCompletionRequest,
    EnrollmentReceipt,
)
from .models import validate_canonical_audience_origin
from .repository import (
    EnrolledDeviceRecord,
    EnrollmentChallengeRecord,
    SQLiteDeviceIdentityRepository,
)


PAIRING_SECRET_BYTES = 32
PAIRING_KEY_BYTES = 32
PAIRING_OFFER_TTL_SECONDS = 300
ENROLLMENT_CHALLENGE_TTL_SECONDS = 120
PAIRING_BUNDLE_PREFIX = "JOEOS1"
PAIRING_KEY_INFO = b"joeos.device-enrollment.pairing-key.v1"
TRANSCRIPT_DOMAIN = b"JOEOS-DEVICE-ENROLLMENT-TRANSCRIPT-V1\0"
CLAIM_TRANSCRIPT_DOMAIN = b"JOEOS-DEVICE-ENROLLMENT-CLAIM-V1\0"
SERVER_PROOF_DOMAIN = b"JOEOS-DEVICE-ENROLLMENT-SERVER-PROOF-V1\0"
CLIENT_PROOF_DOMAIN = b"JOEOS-DEVICE-ENROLLMENT-CLIENT-PROOF-V1\0"
PRIVATE_ENROLLMENT_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


class EnrollmentProtocolError(RuntimeError):
    code = "device_enrollment_failed"
    status_code = 401
    public_message = "The device enrollment proof was rejected. Start a new local pairing window."


class EnrollmentConflictError(EnrollmentProtocolError):
    code = "device_enrollment_conflict"
    status_code = 409
    public_message = "The enrollment retry conflicts with an existing enrollment request."


class EnrollmentOriginError(ValueError):
    pass


@dataclass(frozen=True)
class PairingOffer:
    offer_id: UUID
    audience_origin: str
    manual_code: str = field(repr=False)
    expires_at: datetime


class DeviceEnrollmentService:
    """Local-console enrollment with no role, session, approval, or execution grant."""

    def __init__(
        self,
        *,
        repository: SQLiteDeviceIdentityRepository,
        server_id_provider: Callable[[], UUID],
        now_provider: Optional[Callable[[], datetime]] = None,
        random_bytes: Optional[Callable[[int], bytes]] = None,
        uuid_provider: Optional[Callable[[], UUID]] = None,
        allowed_https_hosts: Iterable[str] = (),
        event_sink: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._repository = repository
        self._server_id_provider = server_id_provider
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._random_bytes = random_bytes or secrets.token_bytes
        self._uuid = uuid_provider or uuid4
        self._allowed_https_hosts = frozenset(
            value.strip().rstrip(".").lower()
            for value in allowed_https_hosts
            if value.strip()
        )
        self._event_sink = event_sink

    def prepare(self) -> None:
        self._repository.prepare()
        self._repository.validate_pending_key_material(self._now_epoch())

    def expire_pending(self) -> int:
        return self._repository.expire_pending(self._now_epoch())

    def issue_pairing_offer(
        self,
        audience_origin: str,
        ttl_seconds: int = PAIRING_OFFER_TTL_SECONDS,
    ) -> PairingOffer:
        origin = self.validate_pairing_origin(audience_origin)
        ttl = max(60, min(int(ttl_seconds), PAIRING_OFFER_TTL_SECONDS))
        now = self._now_epoch()
        offer_id = self._new_uuid()
        server_id = self._server_id()
        secret = self._new_random(PAIRING_SECRET_BYTES)
        pairing_key = self._derive_pairing_key(secret, offer_id)
        secret_digest = hashlib.sha256(b"JOEOS-PAIRING-SECRET-V1\0" + secret).digest()
        expires_at = now + ttl
        self._repository.create_offer(
            offer_id=offer_id,
            server_id=server_id,
            audience_origin=origin,
            pairing_key=pairing_key,
            secret_digest=secret_digest,
            created_at=now,
            expires_at=expires_at,
        )
        encoded_secret = base64.b32encode(secret).rstrip(b"=").decode("ascii")
        manual_code = "|".join((PAIRING_BUNDLE_PREFIX, origin, str(offer_id), encoded_secret))
        return PairingOffer(
            offer_id=offer_id,
            audience_origin=origin,
            manual_code=manual_code,
            expires_at=self._datetime(expires_at),
        )

    def validate_pairing_origin(self, audience_origin: str) -> str:
        origin = validate_canonical_audience_origin(audience_origin)
        if not self._origin_is_private(origin):
            raise EnrollmentOriginError(
                "Enrollment requires private HTTPS, Tailscale HTTP, or loopback HTTP."
            )
        return origin

    def create_challenge(
        self,
        payload: EnrollmentChallengeRequest,
    ) -> EnrollmentChallengeResponse:
        now = self._now_epoch()
        if any(
            value.version != 4
            for value in (
                payload.request_id,
                payload.offer_id,
                payload.observed_server_id,
                payload.device.client_instance_id,
            )
        ):
            raise EnrollmentProtocolError()
        request_digest = self._challenge_request_digest(payload)
        existing = self._repository.get_challenge_by_request_id(payload.request_id)
        if existing is not None:
            if not hmac.compare_digest(existing.request_digest, request_digest):
                raise EnrollmentConflictError()
            if (
                existing.server_id != self._server_id()
                or existing.state != "open"
                or existing.expires_at <= now
            ):
                raise EnrollmentProtocolError()
            return self._challenge_response(existing)

        offer = self._repository.get_claimable_offer(payload.offer_id, now)
        if offer is None or not hmac.compare_digest(offer.audience_origin, payload.audience_origin):
            raise EnrollmentProtocolError()

        server_id = self._server_id()
        if offer.server_id != server_id or payload.observed_server_id != server_id:
            raise EnrollmentProtocolError()
        try:
            supplied_claim_proof = base64url_decode(
                payload.claim_proof,
                max_encoded_length=43,
                expected_decoded_length=32,
            )
            expected_claim_proof = self._claim_proof_bytes(offer.pairing_key, payload)
        except ValueError:
            raise EnrollmentProtocolError() from None
        if not hmac.compare_digest(supplied_claim_proof, expected_claim_proof):
            raise EnrollmentProtocolError()

        challenge_id = self._new_uuid()
        device_id = self._new_uuid()
        server_nonce = self._new_random(32)
        client_nonce = base64url_decode(
            payload.client_nonce,
            max_encoded_length=43,
            expected_decoded_length=32,
        )
        authentication = payload.device_authentication_key.parsed
        approval = payload.approval_key.parsed
        if hmac.compare_digest(authentication.fingerprint, approval.fingerprint):
            raise EnrollmentProtocolError()
        expires_at = min(now + ENROLLMENT_CHALLENGE_TTL_SECONDS, offer.expires_at)
        if expires_at <= now:
            raise EnrollmentProtocolError()

        transcript = self._build_transcript(
            server_id=server_id,
            audience_origin=payload.audience_origin,
            offer_id=payload.offer_id,
            request_id=payload.request_id,
            challenge_id=challenge_id,
            device_id=device_id,
            client_instance_id=payload.device.client_instance_id,
            client_nonce=client_nonce,
            server_nonce=server_nonce,
            display_name=payload.device.display_name,
            platform=payload.device.platform,
            os_version=payload.device.os_version,
            app_version=payload.device.app_version,
            auth_public_key=authentication.canonical_der,
            approval_public_key=approval.canonical_der,
            issued_at=now,
            expires_at=expires_at,
        )
        transcript_digest = hashlib.sha256(transcript).digest()
        auth_payload = build_enrollment_signing_envelope(
            server_id=server_id,
            audience_origin=payload.audience_origin,
            offer_id=payload.offer_id,
            request_id=payload.request_id,
            challenge_id=challenge_id,
            device_id=device_id,
            key_purpose="device_authentication",
            key_fingerprint=authentication.fingerprint,
            transcript_sha256=transcript_digest,
            timestamp=now,
            nonce=server_nonce,
        )
        approval_payload = build_enrollment_signing_envelope(
            server_id=server_id,
            audience_origin=payload.audience_origin,
            offer_id=payload.offer_id,
            request_id=payload.request_id,
            challenge_id=challenge_id,
            device_id=device_id,
            key_purpose="approval",
            key_fingerprint=approval.fingerprint,
            transcript_sha256=transcript_digest,
            timestamp=now,
            nonce=server_nonce,
        )
        server_proof = hmac.new(
            offer.pairing_key,
            SERVER_PROOF_DOMAIN + transcript_digest,
            hashlib.sha256,
        ).digest()
        record = EnrollmentChallengeRecord(
            challenge_id=challenge_id,
            request_id=payload.request_id,
            request_digest=request_digest,
            offer_id=payload.offer_id,
            server_id=server_id,
            device_id=device_id,
            audience_origin=payload.audience_origin,
            pairing_key=offer.pairing_key,
            client_instance_id=payload.device.client_instance_id,
            display_name=payload.device.display_name,
            platform=payload.device.platform,
            os_version=payload.device.os_version,
            app_version=payload.device.app_version,
            client_nonce=client_nonce,
            server_nonce=server_nonce,
            auth_public_key=authentication.canonical_spki,
            auth_fingerprint=authentication.fingerprint,
            approval_public_key=approval.canonical_spki,
            approval_fingerprint=approval.fingerprint,
            transcript_digest=transcript_digest,
            server_proof=server_proof,
            auth_payload=auth_payload,
            approval_payload=approval_payload,
            issued_at=now,
            expires_at=expires_at,
            state="open",
            failed_attempts=0,
        )
        if not self._repository.create_challenge(record, now):
            replay = self._repository.get_challenge_by_request_id(payload.request_id)
            if replay is not None:
                if not hmac.compare_digest(replay.request_digest, request_digest):
                    raise EnrollmentConflictError()
                if (
                    replay.server_id == server_id
                    and replay.state == "open"
                    and replay.expires_at > now
                ):
                    return self._challenge_response(replay)
            raise EnrollmentProtocolError()

        return self._challenge_response(record)

    def complete_challenge(
        self,
        challenge_id: UUID,
        payload: EnrollmentCompletionRequest,
    ) -> EnrollmentReceipt:
        request_digest = self._completion_digest(challenge_id, payload)
        existing = self._repository.get_completion(payload.idempotency_key)
        if existing is not None:
            if not hmac.compare_digest(existing.request_digest, request_digest):
                raise EnrollmentConflictError()
            if existing.device.state != "active_unassigned":
                raise EnrollmentProtocolError()
            return self._receipt(existing.device)

        now = self._now_epoch()
        challenge = self._repository.get_open_challenge(challenge_id, now)
        if challenge is None:
            replay = self._repository.get_completion(payload.idempotency_key)
            if (
                replay is not None
                and replay.device.state == "active_unassigned"
                and hmac.compare_digest(replay.request_digest, request_digest)
            ):
                return self._receipt(replay.device)
            raise EnrollmentProtocolError()
        if challenge.server_id != self._server_id() or challenge.pairing_key is None:
            raise EnrollmentProtocolError()
        try:
            supplied_digest = base64url_decode(
                payload.transcript_sha256,
                max_encoded_length=43,
                expected_decoded_length=32,
            )
            supplied_client_proof = base64url_decode(
                payload.client_proof,
                max_encoded_length=43,
                expected_decoded_length=32,
            )
            expected_client_proof = hmac.new(
                challenge.pairing_key,
                CLIENT_PROOF_DOMAIN + challenge.transcript_digest,
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_digest, challenge.transcript_digest):
                raise SignatureVerificationError("Device signature verification failed.")
            if not hmac.compare_digest(supplied_client_proof, expected_client_proof):
                raise SignatureVerificationError("Device signature verification failed.")
            verify_p256_signature(
                challenge.auth_public_key,
                challenge.auth_payload,
                payload.device_authentication_signature,
            )
            verify_p256_signature(
                challenge.approval_public_key,
                challenge.approval_payload,
                payload.approval_signature,
            )
        except (SignatureVerificationError, ValueError):
            self._repository.record_failed_completion(challenge_id, now)
            raise EnrollmentProtocolError() from None

        device = self._repository.complete_challenge(
            challenge=challenge,
            idempotency_key=payload.idempotency_key,
            request_digest=request_digest,
            enrollment_id=self._new_uuid(),
            credential_id=base64url_encode(self._new_random(32)),
            completed_at=now,
        )
        if device is None:
            replay = self._repository.get_completion(payload.idempotency_key)
            if (
                replay is not None
                and replay.device.state == "active_unassigned"
                and hmac.compare_digest(replay.request_digest, request_digest)
            ):
                return self._receipt(replay.device)
            raise EnrollmentConflictError()
        self._emit(
            "success",
            "device-identity",
            "Paired %s as an active device with no assigned role." % device.display_name,
        )
        return self._receipt(device)

    def list_devices(self) -> List[EnrolledDeviceRecord]:
        return self._repository.list_devices()

    def observed_server_id(self) -> UUID:
        """Return the stable non-secret server identity for local operator checks."""

        return self._server_id()

    def revoke_device(self, device_id: UUID, reason: str) -> bool:
        cleaned = reason.strip()
        if not cleaned or len(cleaned) > 240 or any(character in cleaned for character in "\r\n\0"):
            raise ValueError("Revocation reason must be 1 to 240 plain-text characters.")
        revoked = self._repository.revoke_device(device_id, cleaned, self._now_epoch())
        if revoked:
            self._emit("warn", "device-identity", "A locally paired device was revoked.")
        return revoked

    @staticmethod
    def pairing_key_from_manual_code(manual_code: str) -> tuple[str, UUID, bytes]:
        if type(manual_code) is not str or not 80 <= len(manual_code) <= 400:
            raise ValueError("Invalid JoeOS pairing code.")
        parts = manual_code.strip().split("|")
        if len(parts) != 4 or parts[0] != PAIRING_BUNDLE_PREFIX:
            raise ValueError("Invalid JoeOS pairing code.")
        origin = validate_canonical_audience_origin(parts[1])
        try:
            offer_id = UUID(parts[2])
            padding = "=" * ((8 - len(parts[3]) % 8) % 8)
            secret = base64.b32decode(parts[3] + padding, casefold=False)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid JoeOS pairing code.") from None
        if offer_id.version != 4 or len(secret) != PAIRING_SECRET_BYTES:
            raise ValueError("Invalid JoeOS pairing code.")
        canonical_secret = base64.b32encode(secret).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(canonical_secret, parts[3]):
            raise ValueError("Invalid JoeOS pairing code.")
        return origin, offer_id, secret

    @staticmethod
    def derive_pairing_key_from_manual_code(manual_code: str) -> tuple[str, UUID, bytes]:
        origin, offer_id, secret = DeviceEnrollmentService.pairing_key_from_manual_code(manual_code)
        return origin, offer_id, DeviceEnrollmentService._derive_pairing_key(secret, offer_id)

    @staticmethod
    def _derive_pairing_key(secret: bytes, offer_id: UUID) -> bytes:
        if type(secret) is not bytes or len(secret) != PAIRING_SECRET_BYTES:
            raise ValueError("Pairing secrets must contain exactly 32 bytes.")
        extracted = hmac.new(offer_id.bytes, secret, hashlib.sha256).digest()
        return hmac.new(extracted, PAIRING_KEY_INFO + b"\x01", hashlib.sha256).digest()[:PAIRING_KEY_BYTES]

    @staticmethod
    def client_proof(pairing_key: bytes, transcript_digest: bytes) -> str:
        if (
            type(pairing_key) is not bytes
            or type(transcript_digest) is not bytes
            or len(pairing_key) != 32
            or len(transcript_digest) != 32
        ):
            raise ValueError("Pairing proof inputs must contain exactly 32 bytes.")
        return base64url_encode(
            hmac.new(pairing_key, CLIENT_PROOF_DOMAIN + transcript_digest, hashlib.sha256).digest()
        )

    @staticmethod
    def claim_proof(
        pairing_key: bytes,
        payload: EnrollmentChallengeRequest,
    ) -> str:
        return base64url_encode(
            DeviceEnrollmentService._claim_proof_bytes(pairing_key, payload)
        )

    @staticmethod
    def _claim_proof_bytes(
        pairing_key: bytes,
        payload: EnrollmentChallengeRequest,
    ) -> bytes:
        if type(pairing_key) is not bytes or len(pairing_key) != PAIRING_KEY_BYTES:
            raise ValueError("Pairing proof keys must contain exactly 32 bytes.")
        values = (
            str(payload.observed_server_id).encode("ascii"),
            payload.audience_origin.encode("ascii"),
            str(payload.offer_id).encode("ascii"),
            str(payload.request_id).encode("ascii"),
            str(payload.device.client_instance_id).encode("ascii"),
            base64url_decode(
                payload.client_nonce,
                max_encoded_length=43,
                expected_decoded_length=32,
            ),
            payload.device.display_name.encode("utf-8"),
            payload.device.platform.encode("ascii"),
            payload.device.os_version.encode("utf-8"),
            payload.device.app_version.encode("utf-8"),
            payload.device_authentication_key.parsed.canonical_der,
            payload.approval_key.parsed.canonical_der,
        )
        transcript = CLAIM_TRANSCRIPT_DOMAIN + b"".join(
            len(value).to_bytes(4, "big") + value for value in values
        )
        return hmac.new(pairing_key, transcript, hashlib.sha256).digest()

    @staticmethod
    def _build_transcript(**fields) -> bytes:
        values = (
            str(fields["server_id"]).encode("ascii"),
            fields["audience_origin"].encode("ascii"),
            str(fields["offer_id"]).encode("ascii"),
            str(fields["request_id"]).encode("ascii"),
            str(fields["challenge_id"]).encode("ascii"),
            str(fields["device_id"]).encode("ascii"),
            str(fields["client_instance_id"]).encode("ascii"),
            fields["client_nonce"],
            fields["server_nonce"],
            fields["display_name"].encode("utf-8"),
            fields["platform"].encode("ascii"),
            fields["os_version"].encode("utf-8"),
            fields["app_version"].encode("utf-8"),
            fields["auth_public_key"],
            fields["approval_public_key"],
            str(fields["issued_at"]).encode("ascii"),
            str(fields["expires_at"]).encode("ascii"),
        )
        return TRANSCRIPT_DOMAIN + b"".join(
            len(value).to_bytes(4, "big") + value for value in values
        )

    @staticmethod
    def _completion_digest(challenge_id: UUID, payload: EnrollmentCompletionRequest) -> bytes:
        document = payload.model_dump(mode="json")
        document["challenge_id"] = str(challenge_id)
        for field_name in (
            "device_authentication_signature",
            "approval_signature",
        ):
            try:
                document[field_name] = canonicalize_p256_signature(document[field_name])
            except SignatureVerificationError:
                # Invalid encodings are still hashed deterministically and then fail verification.
                pass
        canonical = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(canonical).digest()

    @staticmethod
    def _challenge_request_digest(payload: EnrollmentChallengeRequest) -> bytes:
        canonical = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(canonical).digest()

    def _challenge_response(
        self,
        challenge: EnrollmentChallengeRecord,
    ) -> EnrollmentChallengeResponse:
        return EnrollmentChallengeResponse(
            request_id=challenge.request_id,
            challenge_id=challenge.challenge_id,
            offer_id=challenge.offer_id,
            device_id=challenge.device_id,
            observed_server_id=challenge.server_id,
            audience_origin=challenge.audience_origin,
            issued_at=self._datetime(challenge.issued_at),
            expires_at=self._datetime(challenge.expires_at),
            server_nonce=base64url_encode(challenge.server_nonce),
            transcript_sha256=base64url_encode(challenge.transcript_digest),
            server_proof=base64url_encode(challenge.server_proof),
            device_authentication_payload=base64url_encode(challenge.auth_payload),
            approval_payload=base64url_encode(challenge.approval_payload),
        )

    def _receipt(self, device: EnrolledDeviceRecord) -> EnrollmentReceipt:
        return EnrollmentReceipt(
            enrollment_id=device.enrollment_id,
            device_id=device.device_id,
            credential_id=device.credential_id,
            observed_server_id=device.server_id,
            audience_origin=device.audience_origin,
            enrolled_at=self._datetime(device.enrolled_at),
            authentication_key_fingerprint=device.auth_fingerprint,
            approval_key_fingerprint=device.approval_fingerprint,
        )

    def _origin_is_private(self, origin: str) -> bool:
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        if parsed.scheme == "http":
            if host == "localhost" or host.endswith(".localhost"):
                return True
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                return False
            return address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")
        if host in self._allowed_https_hosts:
            return True
        if host == "localhost" or host.endswith((".localhost", ".local", ".ts.net")) or "." not in host:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in PRIVATE_ENROLLMENT_NETWORKS)

    def _server_id(self) -> UUID:
        server_id = self._server_id_provider()
        if not isinstance(server_id, UUID) or server_id.version != 4:
            raise TypeError("Server identity provider must return a UUIDv4.")
        return server_id

    def _new_uuid(self) -> UUID:
        value = self._uuid()
        if not isinstance(value, UUID) or value.version != 4:
            raise TypeError("Identity UUID provider must return UUIDv4 values.")
        return value

    def _new_random(self, size: int) -> bytes:
        value = self._random_bytes(size)
        if type(value) is not bytes or len(value) != size:
            raise TypeError("Identity random provider returned an invalid byte sequence.")
        return value

    def _now_epoch(self) -> int:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise TypeError("Identity time provider must return timezone-aware UTC.")
        return int(value.timestamp())

    @staticmethod
    def _datetime(epoch: int) -> datetime:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    def _emit(self, level: str, source: str, message: str) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(level, source, message)
        except Exception:
            return
