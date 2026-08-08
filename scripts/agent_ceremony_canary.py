#!/usr/bin/env python3
"""Full Agent Fabric ceremony canary (development tool).

Drives the real HTTP API exactly as the deployed browser will:
  bootstrap -> pairing offer (host) -> browser enrollment (P-256) ->
  device assignment (host) -> auth challenge -> solve -> session ->
  list providers/models/agents -> start Architect run -> execute ->
  persisted result -> delegation (Joe -> Architect) -> council member runs.

Uses a scratch backend bound to a copy of the live database and the real local
Ollama runtime. This proves the authoritative browser path end-to-end with real
local inference. No browser code is involved here; this is the ceremony driver
that the browser will replicate with WebCrypto.

Usage:
    python scripts/agent_ceremony_canary.py [--db /path/to/joeos.db]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from server.identity.crypto import (  # noqa: E402
    base64url_encode,
    build_enrollment_signing_envelope,
    canonicalize_p256_signature,
)
from server.identity.enrollment_models import (  # noqa: E402
    EnrollmentCompletionRequest,
    EnrollmentPublicKey,
)
from server.identity.service import DeviceEnrollmentService  # noqa: E402

import joeos_backend  # noqa: E402

ORIGIN = "https://mcso9tqzb9.tailb9395f.ts.net"


def _b32(value: bytes) -> str:
    return base64.b32encode(value).rstrip(b"=").decode("ascii")


def _uuid() -> UUID:
    return UUID(int=secrets.randbits(128), version=4)


def _derive_pairing_key(secret: bytes, offer_id: UUID) -> bytes:
    extracted = hmac.new(offer_id.bytes, secret, hashlib.sha256).digest()
    return hmac.new(extracted, b"joeos.device-enrollment.pairing-key.v1" + b"\x01",
                    hashlib.sha256).digest()[:32]


def _claim_proof_bytes(pairing_key: bytes, payload) -> bytes:
    CLAIM_DOMAIN = b"JOEOS-DEVICE-ENROLLMENT-CLAIM-V1\0"
    values = (
        str(payload.observed_server_id).encode("ascii"),
        payload.audience_origin.encode("ascii"),
        str(payload.offer_id).encode("ascii"),
        str(payload.request_id).encode("ascii"),
        str(payload.device.client_instance_id).encode("ascii"),
        base64.urlsafe_b64decode(payload.client_nonce + "==")[:32],
        payload.device.display_name.encode("utf-8"),
        payload.device.platform.encode("ascii"),
        payload.device.os_version.encode("utf-8"),
        payload.device.app_version.encode("utf-8"),
        payload.device_authentication_key.parsed.canonical_der,
        payload.approval_key.parsed.canonical_der,
    )
    transcript = CLAIM_DOMAIN + b"".join(
        len(v).to_bytes(4, "big") + v for v in values
    )
    return hmac.new(pairing_key, transcript, hashlib.sha256).digest()


def _build_transcript(*, server_id, audience_origin, offer_id, request_id,
                       challenge_id, device_id, client_instance_id,
                       client_nonce, server_nonce, display_name, platform,
                       os_version, app_version, auth_pub, approval_pub,
                       issued_at, expires_at) -> bytes:
    DOMAIN = b"JOEOS-DEVICE-ENROLLMENT-TRANSCRIPT-V1\0"
    values = (
        str(server_id).encode("ascii"), audience_origin.encode("ascii"),
        str(offer_id).encode("ascii"), str(request_id).encode("ascii"),
        str(challenge_id).encode("ascii"), str(device_id).encode("ascii"),
        str(client_instance_id).encode("ascii"), client_nonce, server_nonce,
        display_name.encode("utf-8"), platform.encode("ascii"),
        os_version.encode("utf-8"), app_version.encode("utf-8"),
        auth_pub, approval_pub,
        str(issued_at).encode("ascii"), str(expires_at).encode("ascii"),
    )
    return DOMAIN + b"".join(len(v).to_bytes(4, "big") + v for v in values)


def _sign(key: ec.EllipticCurvePrivateKey, message: bytes) -> str:
    der = key.sign(message, ec.ECDSA(hashes.SHA256()))
    return canonicalize_p256_signature(base64url_encode(der))


def _spki(key) -> str:
    der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64url_encode(der)


def run_canary(db_path: Path) -> int:
    if not db_path.exists():
        print("FAIL: source db not found: %s" % db_path)
        return 1
    scratch = Path(tempfile.mkdtemp(prefix="joeos-ceremony-"))
    canary_db = scratch / "joeos.db"
    shutil.copy(db_path, canary_db)
    shutil.copy(db_path.parent / "identity-master.key", scratch / "identity-master.key")
    os.environ["JOEOS_DB_PATH"] = str(canary_db)

    results = {}
    try:
        import joeos_backend as backend

        with TestClient(backend.app) as client:
            # ---- 1. bootstrap ----
            boot = client.get("/api/v1/bootstrap").json()
            server_id = UUID(boot["server"]["server_id"])
            results["bootstrap"] = "ok"

            # ---- 2. host issues pairing offer ----
            enrollment = backend.app.state.device_enrollment_service
            offer = enrollment.issue_pairing_offer(ORIGIN)
            origin, offer_id, secret = DeviceEnrollmentService.pairing_key_from_manual_code(
                offer.manual_code
            )
            pairing_key = _derive_pairing_key(secret, offer_id)
            results["offer"] = str(offer_id)

            # ---- 3. browser generates P-256 keys ----
            auth_key = ec.generate_private_key(ec.SECP256R1())
            approval_key = ec.generate_private_key(ec.SECP256R1())
            client_instance_id = _uuid()
            request_id = _uuid()
            client_nonce = secrets.token_bytes(32)

            from server.identity.enrollment_models import EnrollmentChallengeRequest
            challenge_payload = {
                "schema_version": 1,
                "request_id": str(request_id),
                "offer_id": str(offer_id),
                "observed_server_id": str(server_id),
                "audience_origin": origin,
                "client_nonce": base64url_encode(client_nonce),
                "device": {
                    "client_instance_id": str(client_instance_id),
                    "display_name": "JoeOS Browser",
                    "platform": "linux",
                    "os_version": "browser",
                    "app_version": "2.0.0",
                },
                "device_authentication_key": {
                    "algorithm": "ES256",
                    "format": "spki_der_base64url",
                    "value": _spki(auth_key),
                },
                "approval_key": {
                    "algorithm": "ES256",
                    "format": "spki_der_base64url",
                    "value": _spki(approval_key),
                },
                "claim_proof": "A" * 43,
            }
            typed = EnrollmentChallengeRequest(**challenge_payload)
            challenge_payload["claim_proof"] = base64url_encode(
                _claim_proof_bytes(pairing_key, typed)
            )
            challenge_resp = client.post(
                "/api/v1/device-enrollment/challenges", json=challenge_payload
            )
            if challenge_resp.status_code not in (200, 201):
                print("FAIL: enrollment challenge: %s %s" % (
                    challenge_resp.status_code, challenge_resp.text[:300]))
                return 1
            challenge = challenge_resp.json()
            results["challenge_id"] = challenge["challenge_id"]

            # ---- 4. complete enrollment (browser signs server payloads) ----
            transcript_digest = base64.urlsafe_b64decode(
                challenge["transcript_sha256"] + "==")[:32]
            auth_payload = base64.urlsafe_b64decode(
                challenge["device_authentication_payload"] + "==")
            approval_payload = base64.urlsafe_b64decode(
                challenge["approval_payload"] + "==")
            client_proof = base64url_encode(
                hmac.new(pairing_key,
                         b"JOEOS-DEVICE-ENROLLMENT-CLIENT-PROOF-V1\0" + transcript_digest,
                         hashlib.sha256).digest()
            )
            completion = {
                "schema_version": 1,
                "idempotency_key": str(_uuid()),
                "transcript_sha256": challenge["transcript_sha256"],
                "client_proof": client_proof,
                "device_authentication_signature": _sign(auth_key, auth_payload),
                "approval_signature": _sign(approval_key, approval_payload),
            }
            receipt = client.post(
                "/api/v1/device-enrollment/challenges/%s/complete" % challenge["challenge_id"],
                json=completion,
            )
            if receipt.status_code not in (200, 201):
                print("FAIL: enrollment complete: %s %s" % (
                    receipt.status_code, receipt.text[:400]))
                return 1
            device_id = UUID(receipt.json()["device_id"])
            results["device_id"] = str(device_id)

            # ---- 5. host assigns device to owner ----
            authority = backend.app.state.authority_service
            users = authority.list_users()
            orgs = authority.list_organizations()
            workspaces = authority.list_workspaces()
            roles = authority.list_roles()
            owner_role = next(r for r in roles if r["name"] == "joeos.owner")
            authority.assign_device(
                device_id=device_id, user_id=users[0]["id"],
                organization_id=orgs[0]["id"], workspace_id=workspaces[0]["id"],
                role_ids=[owner_role["id"]], assigned_by=users[0]["id"],
            )
            results["assignment"] = "ok"

            # ---- 6. auth challenge -> solve -> session ----
            challenge_req = client.post(
                "/api/v1/auth/challenge",
                json={"device_id": str(device_id), "user_id": str(users[0]["id"])},
            )
            if challenge_req.status_code != 201:
                print("FAIL: auth challenge: %s %s" % (
                    challenge_req.status_code, challenge_req.text[:300]))
                return 1
            auth_challenge = challenge_req.json()
            message = auth_challenge["message"]
            signature = _sign(auth_key, message.encode("ascii"))
            session_resp = client.post(
                "/api/v1/auth/session",
                json={"challenge_id": auth_challenge["challenge_id"], "signature": signature},
            )
            if session_resp.status_code != 200:
                print("FAIL: auth solve: %s %s" % (
                    session_resp.status_code, session_resp.text[:300]))
                return 1
            session = session_resp.json()
            session_id = session["session"]["session_id"]
            results["session_id"] = str(session_id)
            results["refresh_token_issued"] = bool(session.get("refresh_token"))

            # ---- 7. principal + control plane with session header ----
            headers = {"X-Joeos-Session": session_id}
            principal = client.get("/api/v1/principal", headers=headers)
            if principal.status_code != 200:
                print("FAIL: principal: %s" % principal.status_code)
                return 1
            capabilities = principal.json()["capabilities"]
            results["principal_caps"] = len(capabilities)

            providers = client.get("/api/v1/control/providers", headers=headers).json()["providers"]
            models = client.get("/api/v1/control/models", headers=headers).json()["models"]
            agents = client.get("/api/v1/control/agents", headers=headers).json()["agents"]
            tools = client.get("/api/v1/control/tools", headers=headers).json()["tools"]
            results["providers"] = len(providers)
            results["models"] = len(models)
            results["agents"] = len(agents)
            results["tools"] = len(tools)

            architect = next(a for a in agents if a["key"] == "joeos.architect")
            # This VPS (7.8 GiB) can cold-load the 7B family only in isolation.
            # For the end-to-end canary we pin a model that reliably fits under
            # the combined backend+browser load; production bindings prefer the
            # 7B family and the reported model is always the one actually used.
            architect_run = client.post(
                "/api/v1/control/agents/%s/runs" % architect["id"],
                headers=headers,
                json={
                    "conversation_id": str(_uuid()), "message_id": str(_uuid()),
                    "model_preference": os.getenv(
                        "JOEOS_CANARY_MODEL", "qwen2.5-coder:1.5b"),
                    "objective": "Describe your configured JoeOS role in no more than five bullet points. Do not use tools and do not modify anything.",
                },
            )
            if architect_run.status_code != 202:
                print("FAIL: start run: %s %s" % (
                    architect_run.status_code, architect_run.text[:300]))
                return 1
            run = architect_run.json()
            run_id = run["id"]
            results["run_id"] = str(run_id)

            executed = client.post(
                "/api/v1/control/runs/%s/execute" % run_id, headers=headers, json={}
            )
            if executed.status_code != 200:
                print("FAIL: execute run: %s %s" % (
                    executed.status_code, executed.text[:300]))
                return 1
            executed = executed.json()
            results["run_status"] = executed["status"]
            results["run_model"] = executed.get("model_key")
            results["run_provider"] = executed.get("provider_key")
            results["run_result"] = (executed.get("result") or "")[:80]

            refreshed = client.get("/api/v1/control/runs/%s" % run_id, headers=headers).json()
            results["refresh_status"] = refreshed["status"]
            results["refresh_result_persisted"] = bool(refreshed.get("result"))
            results["refresh_model"] = refreshed.get("model_key")

            # ---- 8. delegation: Joe -> Architect ----
            joe = next(a for a in agents if a["key"] == "joeos.joe")
            joe_run = client.post(
                "/api/v1/control/agents/%s/runs" % joe["id"], headers=headers,
                json={
                    "conversation_id": str(_uuid()), "message_id": str(_uuid()),
                    "objective": "Ask Architect to inspect the current JoeOS agent architecture documentation and identify the single most important architectural strength, then summarize Architect's answer. Do not modify files.",
                },
            ).json()
            delegations = client.get(
                "/api/v1/control/runs/%s/delegations" % joe_run["id"], headers=headers
            ).json()["delegations"]
            results["delegations_before"] = len(delegations)
            child = client.post(
                "/api/v1/control/runs/%s/delegate" % joe_run["id"], headers=headers,
                json={"agent_id": architect["id"], "objective": "Identify the single most important architectural strength of the current JoeOS agent architecture."},
            )
            if child.status_code != 200:
                print("FAIL: delegate: %s %s" % (
                    child.status_code, child.text[:300]))
                return 1
            child_run = child.json()
            results["child_run_id"] = str(child_run["id"])
            results["child_parent"] = str(child_run["parent_run_id"])
            results["child_status"] = child_run["status"]

            # ---- 9. summary ----
            ok = (
                results.get("run_status") == "succeeded"
                and results.get("run_model") is not None
                and results.get("run_provider") == "ollama"
                and results.get("refresh_result_persisted") is True
                and results.get("providers", 0) >= 1
                and results.get("models", 0) >= 1
                and results.get("agents", 0) >= 6
                and results.get("child_status") == "succeeded"
            )
            results["ok"] = bool(ok)
            print(json.dumps(results, indent=2, default=str))
            return 0 if ok else 1
    finally:
        os.environ.pop("JOEOS_DB_PATH", None)
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent-ceremony-canary")
    parser.add_argument("--db", default=str(BASE / "data" / "joeos.db"))
    args = parser.parse_args()
    return run_canary(Path(args.db))


if __name__ == "__main__":
    sys.exit(main())
