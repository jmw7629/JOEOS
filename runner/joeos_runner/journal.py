"""Bounded, tamper-evident local execution journal.

Append-only entries chained by SHA-256 digest of the previous entry. The journal
is recovery evidence only; backend records remain authoritative. Secrets,
credentials, private keys, and unrestricted output are never written here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

MAX_ENTRIES = 2_000
MAX_ENTRY_BYTES = 4_096


class JournalError(Exception):
    pass


@dataclass(frozen=True)
class JournalEntry:
    sequence: int
    previous_digest: str
    digest: str
    runner_id: str
    job_id: str
    lease_generation: int
    state: str
    executor: str
    timestamp: int
    result_metadata: str


class ExecutionJournal:
    def __init__(self, path: str, runner_id: str) -> None:
        self._path = Path(path)
        self._runner_id = runner_id

    def append(self, *, job_id: str, lease_generation: int, state: str,
               executor: str = "", result_metadata: str = "") -> JournalEntry:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entries = self.entries()
        previous = entries[-1].digest if entries else "0" * 64
        sequence = (entries[-1].sequence + 1) if entries else 1
        body = {
            "sequence": sequence, "previous_digest": previous, "runner_id": self._runner_id,
            "job_id": job_id, "lease_generation": lease_generation, "state": state,
            "executor": executor, "timestamp": _now_ms(),
            "result_metadata": result_metadata[:1_000],
        }
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        body["digest"] = digest
        line = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if len(line) > MAX_ENTRY_BYTES:
            raise JournalError("journal entry exceeds the bound")
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if len(self.entries()) > MAX_ENTRIES:
            self._retain(MAX_ENTRIES)
        return JournalEntry(sequence, previous, digest, self._runner_id, job_id,
                            lease_generation, state, executor, body["timestamp"], body["result_metadata"])

    def entries(self) -> List[JournalEntry]:
        if not self._path.exists():
            return []
        rows = []
        previous = "0" * 64
        with open(self._path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    body = json.loads(line)
                except json.JSONDecodeError as error:
                    raise JournalError("journal entry is not valid JSON: %s" % error) from error
                if str(body.get("previous_digest", "")) != previous:
                    raise JournalError("journal digest chain broken at sequence %s" % body.get("sequence"))
                canonical = {k: v for k, v in body.items() if k != "digest"}
                recomputed = hashlib.sha256(
                    json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if str(body.get("digest", "")) != recomputed:
                    raise JournalError("journal entry digest mismatch at sequence %s" % body.get("sequence"))
                previous = str(body.get("digest"))
                rows.append(JournalEntry(
                    sequence=int(body["sequence"]), previous_digest=str(body["previous_digest"]),
                    digest=str(body["digest"]), runner_id=str(body["runner_id"]),
                    job_id=str(body["job_id"]), lease_generation=int(body["lease_generation"]),
                    state=str(body["state"]), executor=str(body.get("executor", "")),
                    timestamp=int(body["timestamp"]),
                    result_metadata=str(body.get("result_metadata", "")),
                ))
        return rows

    def verify(self) -> bool:
        try:
            self.entries()
            return True
        except JournalError:
            return False

    def active_jobs(self) -> List[Dict]:
        """Locally recorded non-terminal jobs (recovery evidence)."""
        seen: Dict[str, Dict] = {}
        for entry in self.entries():
            if entry.job_id in ("", "none"):
                continue
            if entry.state in ("running", "queued", "leased", "acknowledged", "cancellation_requested"):
                seen[entry.job_id] = {
                    "job_id": entry.job_id,
                    "lease_generation": entry.lease_generation,
                    "executor": entry.executor,
                    "state": entry.state,
                }
            elif entry.state in ("succeeded", "failed", "cancelled", "timed_out", "interrupted"):
                seen.pop(entry.job_id, None)
        return list(seen.values())

    def _retain(self, limit: int) -> None:
        lines = self._path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= limit:
            return
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=str(self._path.parent), delete=False, encoding="utf-8"
        )
        try:
            with tmp:
                tmp.write("\n".join(lines[-limit:]) + "\n")
            os.replace(tmp.name, self._path)
        finally:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)
