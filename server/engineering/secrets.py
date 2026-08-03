"""Secret protection: detection, masking, path classification, and scanning.

Never reveal complete secret values. Masking keeps only the first few and last
few safe characters plus the value category.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .models import SecretMatch, SecretPolicy, SecretScanResult

SECRET_PATH_NAMES: Tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.example",
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    "id_ecdsa",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "*.jks",
    "*.mobileprovision",
    "service-account.json",
    "credentials.json",
    "config.json",
    "id_ed25519.pub",
    "*.secret",
)

_PATTERN_DEFS: Tuple[Tuple[str, List[re.Pattern]], ...] = (
    (
        "dotenv",
        [
            re.compile(r"(?i)^\s*(?:export\s+)?[A-Z][A-Z0-9_]{1,63}\s*=\s*(['\"]?)(.{6,}?)\1\s*$"),
            re.compile(r"\b(?:api[_-]?key|secret|token|password|passwd)\b.{0,40}=\s*(['\"]?)(.{6,})\1", re.I),
        ],
    ),
    (
        "private_key",
        [
            re.compile(r"-----BEGIN\s+(?:RSA|EC|OPENSSH|DSA|PGP|ENCRYPTED)\s+PRIVATE\s+KEY-----"),
        ],
    ),
    (
        "github_token",
        [
            re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        ],
    ),
    (
        "openai_token",
        [
            re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
        ],
    ),
    (
        "stripe_token",
        [
            re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
            re.compile(r"\brk_live_[A-Za-z0-9]{20,}\b"),
        ],
    ),
    (
        "aws_credential",
        [
            re.compile(r"AKIA[0-9A-Z]{16}\b"),
            re.compile(r"ASIA[0-9A-Z]{16}\b"),
        ],
    ),
    (
        "google_credential",
        [
            re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
            re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}\b"),
        ],
    ),
    (
        "jwt",
        [
            re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        ],
    ),
    (
        "connection_string",
        [
            re.compile(r"(?i)(postgres(?:ql)?|mysql|redis|amqp|mongodb)(\+srv)?://[^:\s]+:[^@\s]+@"),
        ],
    ),
)

MAX_SCAN_BYTES = 2_000_000
MAX_MATCHES_PER_FILE = 20


def _mask(value: str) -> str:
    if not value:
        return "********"
    safe_prefix = "".join(ch for ch in value[:4] if ch.isalnum() or ch in "_-") or "****"
    safe_suffix = "".join(ch for ch in value[-4:] if ch.isalnum() or ch in "_-") or "****"
    return "%s…%s" % (safe_prefix, safe_suffix)


def mask_value(value: str, category: str) -> str:
    return "[%s] %s" % (category, _mask(value))


def is_secret_path(rel_path: str) -> bool:
    name = Path(rel_path).name
    for pattern in SECRET_PATH_NAMES:
        if re.fullmatch(pattern.replace(".", r"\.").replace("*", ".*"), name):
            return True
    return name.startswith(".env")


def detect_secrets(
    text: str,
    file: str,
    source: str = "policy",
    max_matches: int = 100,
) -> List[SecretMatch]:
    matches: List[SecretMatch] = []
    for category, patterns in _PATTERN_DEFS:
        for pattern in patterns:
            for found in pattern.finditer(text):
                groups = found.groups()
                candidates = groups if groups else (found.group(0),)
                for group in candidates:
                    if not group:
                        continue
                    stripped = group.strip().strip("'\"")
                    if len(stripped) < 6:
                        continue
                    line = text.count("\n", 0, found.start()) + 1
                    matches.append(
                        SecretMatch(
                            file=file,
                            line=line,
                            category=category,
                            masked=mask_value(stripped, category),
                            confidence="high" if category in {
                                "private_key",
                                "github_token",
                                "aws_credential",
                                "stripe_token",
                                "google_credential",
                            } else "medium",
                            source=source,
                            remediation=_remediation(category),
                        )
                    )
                    if len(matches) >= max_matches:
                        return matches
                if len(matches) >= max_matches:
                    return matches
    return matches


def _remediation(category: str) -> str:
    return {
        "dotenv": "Rotate the value, add the file to .gitignore, and never commit it.",
        "private_key": "Rotate the key and remove it from the repository.",
        "github_token": "Revoke the token in GitHub settings and remove it.",
        "openai_token": "Rotate the API key and remove it from tracked files.",
        "stripe_token": "Rotate the secret key in the Stripe dashboard.",
        "aws_credential": "Rotate the credential in IAM and disable it.",
        "google_credential": "Rotate the credential in Google Cloud IAM.",
        "jwt": "Treat the token as compromised and reissue it.",
        "connection_string": "Rotate the credential and move it to a secret store.",
    }.get(category, "Rotate the value and remove it from tracked files.")


def scan_file(path: Path, root: Path, source: str = "scan") -> List[SecretMatch]:
    try:
        if not path.is_file():
            return []
        if path.stat().st_size > MAX_SCAN_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel = str(path.relative_to(root))
    return detect_secrets(text, rel, source=source, max_matches=MAX_MATCHES_PER_FILE)


class SecretProtector:
    def policy(self) -> SecretPolicy:
        return SecretPolicy(
            masked_categories=tuple(category for category, _ in _PATTERN_DEFS),
            secret_path_names=SECRET_PATH_NAMES,
            protected_operations=(
                "open file",
                "search excerpt",
                "preview",
                "AI context",
                "diagnostics",
                "activity summary",
                "documentation generation",
            ),
            notice=(
                "Likely secret values are masked. Editing, deleting, or sharing secret-bearing "
                "files requires explicit approval. Never send secret content to models unless "
                "an explicit policy allows it."
            ),
        )

    def scan_text(
        self,
        text: str,
        file: str,
        source: str = "policy",
        max_matches: int = 100,
    ) -> List[SecretMatch]:
        return detect_secrets(text, file, source=source, max_matches=max_matches)

    def mask_text(self, text: str, file: str) -> Tuple[str, int]:
        matches = detect_secrets(text, file, max_matches=200)
        if not matches:
            return text, 0
        lines = text.split("\n")
        for line_number in sorted({item.line for item in matches}):
            if 1 <= line_number <= len(lines):
                lines[line_number - 1] = "<redacted by JoeOS secret protection>"
        return "\n".join(lines), len(matches)

    def scan_repository(
        self,
        root: Path,
        skip: Sequence[str] = (".git",),
        limit_files: int = 500,
        max_matches: int = 100,
    ) -> SecretScanResult:
        skip_set = set(skip)
        matches: List[SecretMatch] = []
        scanned = 0
        truncated = False
        for path in sorted(root.rglob("*")):
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if any(part in skip_set for part in relative.parts):
                continue
            if path.is_dir():
                continue
            scanned += 1
            if scanned > limit_files:
                truncated = True
                break
            if is_secret_path(str(relative)) or _looks_like_key(path):
                continue
            for match in scan_file(path, root):
                matches.append(match)
                if len(matches) >= max_matches:
                    return SecretScanResult(
                        matches=tuple(matches),
                        files_scanned=scanned,
                        truncated=True,
                    )
        return SecretScanResult(matches=tuple(matches), files_scanned=scanned, truncated=truncated)


def _looks_like_key(path: Path) -> bool:
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return False
    return (
        b"-----BEGIN" in data
        or (b"PRIVATE KEY" in data and b"KEY" in data)
        or (len(data) > 64 and _entropy(data) > 4.6 and path.suffix in {".pem", ".key", ".p12", ".pfx"})
    )


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts: Dict[int, int] = {}
    for byte in data:
        counts[byte] = counts.get(byte, 0) + 1
    length = len(data)
    entropy = 0.0
    for count in counts.values():
        probability = count / length
        entropy -= probability * (probability and (probability * 32).bit_length() or 1)
    return entropy


def content_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
