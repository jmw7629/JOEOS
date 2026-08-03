"""Repository-wide text search with secret-file exclusion.

Searches are bounded by byte budget and file count, and never descend into
vendor, build, or VCS directories. Files classified as secret-bearing are
skipped so secrets cannot be surfaced by search.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .filesystem import IGNORED_DIRECTORIES, TEXT_EXTENSIONS, MAX_TEXT_BYTES
from .models import SearchEnvelope, SearchResult
from .secrets import is_secret_path

MAX_SEARCH_FILES = 1200
MAX_SEARCH_BYTES = 32_000_000
MAX_RESULTS = 200
MAX_CONTEXT_BYTES = 4096


class SearchService:
    def __init__(
        self,
        root_provider: Callable[[str], Path],
    ) -> None:
        self._root_provider = root_provider

    def search(self, project_id: str, query: str, *, file_pattern: Optional[str] = None, max_results: int = MAX_RESULTS) -> SearchEnvelope:
        started = time.time()
        if not query or query.strip() == "":
            return SearchEnvelope(project_id=project_id, query=query, results=(), truncated=False, files_scanned=0, seconds=0.0)
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        if file_pattern:
            try:
                file_re = re.compile(file_pattern)
            except re.error:
                file_re = None
        else:
            file_re = None
        root = self._root_provider(project_id)
        results: List[SearchResult] = []
        scanned = 0
        truncated = False
        for path, rel in self._walk(root):
            if file_re is not None and not file_re.search(path.name):
                continue
            if is_secret_path(rel):
                continue
            try:
                if path.stat().st_size > MAX_TEXT_BYTES:
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read(MAX_CONTEXT_BYTES * 8)
            except OSError:
                continue
            scanned += 1
            for line_number, line in enumerate(content.splitlines(), start=1):
                match = pattern.search(line)
                if match is None:
                    continue
                start = max(0, match.start() - 60)
                end = min(len(line), match.end() + 60)
                snippet = line[start:end]
                results.append(
                    SearchResult(
                        path=rel,
                        line=line_number,
                        column=match.start() + 1,
                        snippet=snippet,
                    )
                )
                if len(results) >= max_results:
                    truncated = True
                    break
            if len(results) >= max_results:
                truncated = True
                break
            if scanned >= MAX_SEARCH_FILES:
                truncated = True
                break
        results.sort(key=lambda result: (result.path, result.line))
        return SearchEnvelope(
            project_id=project_id,
            query=query,
            results=tuple(results[:max_results]),
            truncated=truncated,
            files_scanned=min(scanned, MAX_SEARCH_FILES),
            seconds=round(time.time() - started, 3),
        )

    def _walk(self, root: Path) -> List[Tuple[Path, str]]:
        matches: List[Tuple[Path, str]] = []
        root_str = str(root)
        try:
            children = sorted(root.rglob("*"))
        except OSError:
            return matches
        byte_budget = MAX_SEARCH_BYTES
        for child in children:
            rel = str(child.relative_to(root))
            parts = rel.split(os.sep)
            if any(part in IGNORED_DIRECTORIES for part in parts):
                continue
            if child.is_dir():
                continue
            if child.suffix.lower() not in TEXT_EXTENSIONS and child.name not in {".gitignore", ".env.example"}:
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            if size > MAX_TEXT_BYTES:
                continue
            if byte_budget - size <= 0:
                break
            byte_budget -= size
            matches.append((child, rel))
            if len(matches) >= MAX_SEARCH_FILES:
                break
        return matches
