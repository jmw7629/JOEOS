"""Content safety for the JoeOS Communications Platform.

Message content is always treated as untrusted. This module sanitizes HTML,
blocks remote content, checks link safety, computes phishing/impersonation
signals, and marks prompt-injection attempts so content can never become
authority.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple

ALLOWED_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "code", "pre", "blockquote",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "a", "span",
}
ALLOWED_ATTRS = {"a": {"href", "title"}, "span": set()}
DANGEROUS_PROTOCOLS = {"javascript:", "data:text/html", "vbscript:", "file:"}
REMOTE_SCHEMES = {"http:", "https:"}
SUSPICIOUS_DOMAINS_PATTERN = re.compile(r"[\u0400-\u04FF\u4E00-\u9FFF\u30A0-\u30FF\uAC00-\uD7AF]")


class SanitizationError(RuntimeError):
    pass


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ALLOWED_TAGS:
            allowed = [item for item in attrs if item[0] in ALLOWED_ATTRS.get(tag, set())]
            if tag == "a":
                filtered = []
                for key, value in allowed:
                    if key == "href":
                        value = sanitize_link(value)
                        if value is None:
                            continue
                        filtered.append((key, value))
                allowed = filtered
            attr_text = "".join(' %s="%s"' % (key, html_escape(value)) for key, value in allowed)
            self.parts.append("<%s%s>" % (tag, attr_text))
        elif tag in {"script", "style", "form", "iframe", "object", "embed", "link", "meta", "img"}:
            self._skip_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ALLOWED_TAGS:
            self.parts.append("</%s>" % tag)
        elif tag in {"script", "style", "form", "iframe", "object", "embed", "link", "meta", "img"}:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(html_escape(data))

    def handle_entityref(self, name):
        if self._skip_depth == 0:
            self.parts.append("&%s;" % name)

    def handle_charref(self, name):
        if self._skip_depth == 0:
            self.parts.append("&#%s;" % name)


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


def sanitize_html(html: str) -> str:
    """Strip scripts, forms, events, dangerous protocols, and remote content."""
    if not html:
        return ""
    parser = _Sanitizer()
    try:
        parser.feed(html)
    except Exception as exc:
        raise SanitizationError("content could not be sanitized.") from exc
    result = "".join(parser.parts)
    # Strip inline event handlers and dangerous hrefs at the text level.
    result = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", result)
    result = re.sub(r"\son\w+\s*=\s*$", "", result)
    return result


def sanitize_link(value: str) -> Optional[str]:
    """Return a safe href or None to drop the link entirely."""
    value = (value or "").strip()
    lowered = value.lower()
    for protocol in DANGEROUS_PROTOCOLS:
        if lowered.startswith(protocol):
            return None
    if lowered.startswith("#"):
        return None
    if lowered.startswith("mailto:") or lowered.startswith("tel:"):
        return value
    if lowered.startswith(("http:", "https:")):
        return value
    return None


def remote_content_links(html: str) -> Tuple[str, ...]:
    """Extract remote image/media/font URLs to block by default."""
    urls = set()
    for match in re.finditer(r'(?:src|href|background)\s*=\s*["\']([^"\']+)["\']', html, re.I):
        url = match.group(1)
        if url.lower().startswith(("http:", "https:")):
            urls.add(url)
    return tuple(sorted(urls))


def analyze_links(links: Sequence[str]) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """Return (warnings_per_link, metadata_per_link) for link safety."""
    warnings: Dict[str, List[str]] = {}
    metadata: Dict[str, str] = {}
    for link in links:
        item_warnings: List[str] = []
        lowered = link.lower()
        if lowered.startswith("http:"):
            item_warnings.append("plain HTTP is not encrypted")
        if "//" in link:
            domain = link.split("//", 1)[1].split("/", 1)[0].split("@")[-1].split(":")[0]
            if not domain:
                continue
            if domain in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
                item_warnings.append("local-network target")
            if SUSPICIOUS_DOMAINS_PATTERN.search(domain):
                item_warnings.append("possible lookalike or non-ASCII domain")
            if "xn--" in domain:
                item_warnings.append("punycode domain")
            metadata[link] = domain
        if re.search(r"\.(?:exe|msi|bat|cmd|sh|ps1|dmg|apk)$", link.lower()):
            item_warnings.append("downloadable executable")
        if item_warnings:
            warnings[link] = item_warnings
    return warnings, metadata


PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore (all |any )?(previous|prior|earlier) instructions", re.I),
    re.compile(r"disregard (your|the previous) (instructions|rules)", re.I),
    re.compile(r"you are now .*?(without|no|ignore).*?(rules|restrictions|safety)", re.I),
    re.compile(r"grant (yourself |the )?(tool|shell|file|network) access", re.I),
    re.compile(r"approve (this|the) (action|request|transaction)", re.I),
    re.compile(r"reveal (your|the) (secret|token|password|api key)", re.I),
    re.compile(r"send (this|the) (message|reply|email)", re.I),
    re.compile(r"disable (all )?(security|safety|approval) (checks|features|controls)", re.I),
    re.compile(r"trust this sender", re.I),
)


def prompt_injection_indicators(text: str) -> Tuple[str, ...]:
    """Return prompt-injection indicators present in untrusted content."""
    if not text:
        return ()
    indicators = []
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            indicators.append("instruction-attempt:" + pattern.pattern[:40])
    return tuple(sorted(set(indicators)))


def phishing_signals(
    *,
    sender_display: str,
    sender_address: str,
    reply_to: str = "",
    recipients: Sequence[str] = (),
    body: str = "",
    link_count: int = 0,
    attachment_count: int = 0,
    sender_verified: bool = False,
) -> Tuple[str, ...]:
    """Compute explainable phishing/impersonation indicators (not proof)."""
    signals: List[str] = []
    if not sender_verified:
        signals.append("unverified-sender")
    if reply_to and reply_to.lower() != sender_address.lower():
        signals.append("reply-to-mismatch")
    if sender_display and "@" in sender_display:
        signals.append("display-looks-like-address")
    if re.search(r"urgent|immediate action|act now|password|credential|verify your account", body, re.I):
        signals.append("urgent-or-credential-language")
    if attachment_count and re.search(r"invoice|statement|document", body, re.I):
        signals.append("unexpected-attachment-context")
    if link_count and re.search(r"click (here|the link)|verify", body, re.I):
        signals.append("link-prompt")
    if sender_address and SUSPICIOUS_DOMAINS_PATTERN.search(sender_address.split("@")[-1]):
        signals.append("lookalike-domain")
    return tuple(sorted(set(signals)))


def content_hash(*parts: str) -> str:
    payload = "\x1f".join(parts or ())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()