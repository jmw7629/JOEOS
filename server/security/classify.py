"""Data classification, privacy policy engine, and threat model registry for
the JoeOS Security Platform.

Unknown data classifies conservatively. A model may propose a lower
classification but can never lower it; user/policy remains authoritative. The
privacy engine evaluates data class, source, destination, provider, device,
and consent before processing, storage, routing, display, indexing, export, or
backup.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .policy import SecurityError
from .models import DataClass, PrivacyDecision, ThreatModel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_CLASS_RANK = {
    "public": 0,
    "internal": 1,
    "personal": 2,
    "confidential": 3,
    "restricted": 4,
    "security_sensitive": 5,
    "secret": 6,
    "credential": 7,
    "regulated": 8,
    "unknown": 99,
}


class DataClassificationService:
    """Conservative, typed data classification."""

    def __init__(self, secrets=None) -> None:
        self._secrets = secrets

    def classify(
        self,
        *,
        source: str = "",
        user_label: str = "",
        path: str = "",
        content_hint: str = "",
        proposed_by_model: Optional[str] = None,
    ) -> str:
        """Return the authoritative class. Model proposals can never lower an
        existing or inferred classification."""
        inferred = self._infer(path=path, content_hint=content_hint, source=source)
        if user_label in _CLASS_RANK:
            base = user_label
        else:
            base = inferred
        if proposed_by_model and proposed_by_model in _CLASS_RANK:
            if _CLASS_RANK[proposed_by_model] < _CLASS_RANK[base]:
                return base  # model cannot lower classification
        return base

    def _infer(self, *, path: str, content_hint: str, source: str) -> str:
        lowered_path = path.lower()
        secret_hints = (
            ".env", "secret", "credential", "key.pem", "token", "password",
            "id_rsa", "id_ed25519", ".p12", ".jks", "auth", "credentials",
        )
        if any(hint in lowered_path for hint in secret_hints):
            return "credential"
        if content_hint and self._secrets is not None:
            detections = self._secrets.scan_text(text=content_hint, source="classification")
            if detections:
                return "credential"
        if source in {"external_provider", "web", "message", "document", "attachment"}:
            return "unknown"
        return "internal"


class PrivacyPolicyEngine:
    """Evaluates privacy decisions before processing/routing/storage."""

    def __init__(self, classifications: DataClassificationService) -> None:
        self._classifications = classifications

    def evaluate(
        self,
        *,
        data_class: str,
        source: str = "",
        destination: str = "local",
        provider: str = "",
        device: str = "",
        consent_active: bool = False,
    ) -> Tuple[PrivacyDecision, str]:
        """Return (decision, explanation). Cloud/local decisions are explicit."""
        if data_class in {"secret", "credential", "security_sensitive"}:
            if destination != "local":
                return "block_external_provider", "restricted class cannot leave local processing"
            return "allow_local_processing", "restricted class processed locally"
        if data_class in {"restricted", "confidential", "regulated"}:
            if destination == "cloud":
                if not consent_active:
                    return "require_explicit_consent", "cloud routing requires consent for this class"
                return "block_cloud_ai", "restricted class blocks cloud AI by policy"
            return "allow_local_processing", "processed locally"
        if data_class == "unknown":
            # Unknown defaults conservatively.
            return "allow_local_processing", "unknown class processed locally only"
        if destination == "cloud":
            return "block_cloud_ai", "cloud routing blocked for this class"
        return "allow_local_processing", "allowed"

    def allow_notification_preview(self, *, data_class: str, device: str) -> bool:
        if data_class in {"secret", "credential", "security_sensitive", "regulated", "restricted"}:
            return False
        if device and device.startswith("wearable"):
            return data_class in {"public", "internal"}
        return True

    def allow_semantic_indexing(self, *, data_class: str) -> bool:
        return data_class not in {"secret", "credential", "security_sensitive", "restricted"}

    def allow_backup(self, *, data_class: str) -> bool:
        return data_class not in {"secret", "credential"}


class ThreatModelRegistry:
    """Versioned threat-model records tied to mitigations or residual risk."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def upsert(self, model: ThreatModel) -> ThreatModel:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO threat_models (
                    threat_model_id, subsystem, version, assets, actors, trust_boundaries,
                    entry_points, data_flows, assumptions, threats, mitigations, residual_risk,
                    owner, review_date, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(threat_model_id) DO UPDATE SET
                    version = excluded.version, subsystem = excluded.subsystem,
                    threats = excluded.threats, mitigations = excluded.mitigations,
                    residual_risk = excluded.residual_risk, status = excluded.status,
                    review_date = excluded.review_date
                """,
                (
                    model.threat_model_id, model.subsystem, model.version,
                    "\n".join(model.assets), "\n".join(model.actors),
                    "\n".join(model.trust_boundaries), "\n".join(model.entry_points),
                    "\n".join(model.data_flows), "\n".join(model.assumptions),
                    "\n".join(model.threats), "\n".join(model.mitigations),
                    model.residual_risk, model.owner, model.review_date, model.status,
                    model.created_at or _now(),
                ),
            )
        return self.get(model.threat_model_id)

    def get(self, threat_model_id: str) -> Optional[ThreatModel]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM threat_models WHERE threat_model_id = ?", (threat_model_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self) -> Tuple[ThreatModel, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM threat_models ORDER BY subsystem"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def seed(self) -> None:
        """Seed threat models for the critical boundaries introduced so far."""
        seeds = [
            ("threat.plugin_host", "Plugin Host", ("plugin isolation", "extension host"), ("malicious plugin", "compromised plugin", "prompt injection"), ("host subprocess boundary", "capability broker"), ("plugin package install", "contribution registration"), ("plugin RPC", "capability dispatch"), ("plugins run in subprocess", "capabilities brokered", "permissions granular"), ("unsigned plugin", "integrity mismatch", "permission expansion"), ("subprocess isolation", "permission checks", "integrity verification", "quarantine"), "residual: OS-level sandbox not yet enforced"),
            ("threat.remote_api", "Remote API", ("scoped queries", "sessions"), ("session theft", "replay", "stale approval"), ("mobile gateway", "wearable gateway"), ("authenticate", "scoped query", "command"), ("token flow", "approval flow"), ("sessions expire", "tokens rotate", "allowlist commands"), ("token leakage", "revoked session use"), ("server-side revocation", "short-lived tokens", "exact approval binding"), "residual: local API surface still broad"),
            ("threat.ai_runtime", "AI Runtime", ("model routing", "context"), ("prompt injection", "data exfiltration", "tool abuse"), ("model request", "tool dispatch"), ("prompt", "tool request", "model output"), ("output is untrusted", "tools brokered", "context isolated"), ("model output treated as untrusted",), ("model output as instruction", "tool misuse"), ("tool broker mediation", "output schema validation", "context isolation"), "residual: indirect prompt injection risk"),
        ]
        for threat_model_id, subsystem, assets, actors, boundaries, entry_points, flows, assumptions, threats, mitigations, residual in seeds:
            if self.get(threat_model_id) is None:
                self.upsert(
                    ThreatModel(
                        threat_model_id=threat_model_id,
                        subsystem=subsystem,
                        assets=assets,
                        actors=actors,
                        trust_boundaries=boundaries,
                        entry_points=entry_points,
                        data_flows=flows,
                        assumptions=assumptions,
                        threats=threats,
                        mitigations=mitigations,
                        residual_risk=residual,
                        owner="joeos_security",
                        review_date=_now(),
                        status="active",
                        created_at=_now(),
                    )
                )

    @staticmethod
    def _row(row: sqlite3.Row) -> ThreatModel:
        return ThreatModel(
            threat_model_id=str(row["threat_model_id"]),
            subsystem=str(row["subsystem"]),
            version=int(row["version"]),
            assets=tuple(p for p in str(row["assets"]).split("\n") if p),
            actors=tuple(p for p in str(row["actors"]).split("\n") if p),
            trust_boundaries=tuple(p for p in str(row["trust_boundaries"]).split("\n") if p),
            entry_points=tuple(p for p in str(row["entry_points"]).split("\n") if p),
            data_flows=tuple(p for p in str(row["data_flows"]).split("\n") if p),
            assumptions=tuple(p for p in str(row["assumptions"]).split("\n") if p),
            threats=tuple(p for p in str(row["threats"]).split("\n") if p),
            mitigations=tuple(p for p in str(row["mitigations"]).split("\n") if p),
            residual_risk=str(row["residual_risk"]),
            owner=str(row["owner"]),
            review_date=str(row["review_date"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
        )