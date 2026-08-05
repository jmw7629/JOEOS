"""HTTP transport between the runner daemon and the authoritative backend.

Implements the runner-protocol over a private endpoint. The backend URL is
validated by the configuration (loopback, link-local, RFC 1918, or Tailscale
CGNAT only for plain HTTP; never a public host). All runner-protocol requests
carry the short-lived connection credential in X-Runner-Credential.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class RunnerTransportError(Exception):
    """Raised when the backend refuses or cannot process a runner request."""


class HTTPRunnerTransport:
    """Real HTTP transport for RunnerDaemon (test-injectable only via mocks)."""

    def __init__(
        self,
        backend_url: str,
        *,
        protocol_version: int = 1,
        runner_version: str = "",
        catalog_digest: str = "",
        timeout: float = 10.0,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._protocol_version = protocol_version
        self._runner_version = runner_version
        self._catalog_digest = catalog_digest
        self._timeout = timeout
        self._client: Optional[httpx.Client] = None

    @property
    def _transport(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._backend_url, timeout=self._timeout
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _post(
        self, path: str, payload: Optional[Dict[str, Any]] = None,
        credential: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {}
        if credential:
            headers["X-Runner-Credential"] = credential
        try:
            response = self._transport.post(path, json=payload or {}, headers=headers)
        except httpx.HTTPError as error:
            raise RunnerTransportError(str(error)) from error
        if response.status_code >= 400:
            raise RunnerTransportError(
                "%s %s: %s" % (response.status_code, path, _detail(response))
            )
        return response.json()

    def request_connection(self, runner_id: str, key_identifier: str, public_key: str) -> Dict[str, Any]:
        return self._post(
            "/api/v1/runner/connect/challenge", {"runner_id": str(runner_id)}
        )

    def authenticate(self, challenge: Dict[str, Any], signature_b64url: str) -> Dict[str, Any]:
        return self._post("/api/v1/runner/connect", {
            "challenge_id": str(challenge["challenge_id"]),
            "signature": signature_b64url,
            "protocol_version": self._protocol_version,
            "runner_version": self._runner_version,
            "catalog_digest": self._catalog_digest,
        })

    def heartbeat(self, credential: str) -> bool:
        result = self._post("/api/v1/runner/heartbeat", {}, credential)
        return bool(result.get("ok"))

    def lease(self, credential: str) -> Dict[str, Any]:
        return self._post("/api/v1/runner/lease", {}, credential)

    def acknowledge(self, credential: str, job: Dict[str, Any], signature_b64url: str) -> bool:
        result = self._post(
            "/api/v1/runner/acknowledge",
            {"job_id": str(job["id"]), "signature": signature_b64url},
            credential,
        )
        return bool(result.get("ok"))

    def start(self, credential: str, job: Dict[str, Any]) -> bool:
        result = self._post(
            "/api/v1/runner/start", {"job_id": str(job["id"])}, credential
        )
        return bool(result.get("ok"))

    def progress(self, credential: str, job: Dict[str, Any], text: str) -> bool:
        result = self._post(
            "/api/v1/runner/progress",
            {"job_id": str(job["id"]), "progress": text},
            credential,
        )
        return bool(result.get("ok"))

    def complete(self, credential: str, job: Dict[str, Any], signature_b64url: str, result: Dict[str, Any]) -> Dict[str, Any]:
        return self._post(
            "/api/v1/runner/complete",
            {"job_id": str(job["id"]), "signature": signature_b64url, "result": result},
            credential,
        )

    def rotate(self, credential: str) -> str:
        result = self._post("/api/v1/runner/rotate", {}, credential)
        return str(result.get("connection_credential", ""))


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return response.text[:200]
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            code = detail.get("code")
            message = str(detail.get("message", detail))[:200]
            return ("%s: %s" % (code, message)) if code else message
        return str(detail or body)[:200]
    return str(body)[:200]
