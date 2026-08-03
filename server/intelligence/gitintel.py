"""Git history intelligence: churn, hotspots, ownership, recent commits.

History analysis is bounded (only a configurable number of commits is read)
and treats contributors strictly as recent contributors, never as
authoritative owners. All output is provenance-labeled `git` facts.
"""

from __future__ import annotations

import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .models import ChangeHotspot, GitHistoryStat, GitIntelligence, OwnershipRecord

DEFAULT_DEPTH = 500
MAX_AUTHORS = 12


@dataclass
class CommitInfo:
    commit: str
    author: str
    date: str
    files: Tuple[Tuple[str, int, int], ...]


class GitIntelligenceService:
    def __init__(self, git_binary: str = "git") -> None:
        self._git = git_binary

    def collect(self, project_id: str, repo_root: str, *, depth: int = DEFAULT_DEPTH) -> GitIntelligence:
        now = _now()
        commits = self._read_history(repo_root, depth)
        by_file: Dict[str, Dict[str, object]] = {}
        author_files: Dict[str, Counter] = defaultdict(Counter)
        for commit in commits:
            for rel_path, additions, deletions in commit.files:
                stat = by_file.setdefault(
                    rel_path,
                    {
                        "rel_path": rel_path,
                        "commits": 0,
                        "additions": 0,
                        "deletions": 0,
                        "authors": 0,
                        "churn": 0,
                        "first_commit": None,
                        "last_commit": None,
                    },
                )
                stat["commits"] += 1
                stat["additions"] += additions
                stat["deletions"] += deletions
                stat["churn"] += additions + deletions
                if stat["first_commit"] is None:
                    stat["first_commit"] = commit.commit
                stat["last_commit"] = commit.commit
                author_files[commit.author][rel_path] += 1
        for rel_path in list(by_file):
            by_file[rel_path]["authors"] = sum(1 for counter in author_files.values() if counter.get(rel_path, 0) > 0)
        stats = tuple(
            sorted((GitHistoryStat(**values) for values in by_file.values()), key=lambda s: s.churn, reverse=True)
        )
        hotspots = self._hotspots(author_files, stats)
        ownership = self._ownership(author_files, stats)
        return GitIntelligence(
            project_id=project_id,
            stats=stats,
            hotspots=hotspots,
            ownership=ownership,
            recent_commits=tuple(c.commit for c in commits[:64]),
            history_depth=len(commits),
            generated_at=now,
        )

    def _read_history(self, repo_root: str, depth: int) -> List[CommitInfo]:
        try:
            output = subprocess.run(
                [
                    self._git, "-C", repo_root, "log",
                    "--pretty=%H%x1f%an%x1f%aI",
                    "--numstat",
                    "--no-renames",
                    "-n", str(depth),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.SubprocessError, OSError):
            return []
        if output.returncode != 0:
            return []
        commits: List[CommitInfo] = []
        lines = output.stdout.splitlines()
        index = 0
        while index < len(lines):
            header = lines[index]
            if "\x1f" not in header:
                index += 1
                continue
            commit, author, date = header.split("\x1f", 2)
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            files: List[Tuple[str, int, int]] = []
            while index < len(lines) and lines[index].strip() and "\x1f" not in lines[index]:
                parts = lines[index].split("\t")
                index += 1
                if len(parts) != 3:
                    continue
                additions_raw, deletions_raw, rel_path = parts
                if rel_path.startswith('"'):
                    continue
                try:
                    additions = 0 if additions_raw == "-" else int(additions_raw)
                    deletions = 0 if deletions_raw == "-" else int(deletions_raw)
                except ValueError:
                    continue
                files.append((rel_path, additions, deletions))
            commits.append(CommitInfo(commit, author, date, tuple(files)))
        return commits

    def _hotspots(
        self, author_files: Dict[str, Counter], stats: Tuple[GitHistoryStat, ...]
    ) -> Tuple[ChangeHotspot, ...]:
        if not stats:
            return (ChangeHotspot(rel_path="(no history)", score=0, concern="insufficient_data", factors=()),)
        max_churn = max(s.churn for s in stats) or 1
        contributors_by_path: Dict[str, List[str]] = {}
        for author, counter in author_files.items():
            for rel_path in counter:
                contributors_by_path.setdefault(rel_path, []).append(author)
        hotspots: List[ChangeHotspot] = []
        for stat in stats:
            if stat.commits < 2:
                continue
            ratio = stat.churn / max_churn
            score = int(min(100, ratio * 70 + min(30, stat.commits)))
            factors: List[str] = []
            if stat.commits >= 8:
                factors.append("high commit count")
            if ratio >= 0.7:
                factors.append("high churn relative to repository")
            if stat.authors >= 3:
                factors.append("multiple contributors")
            concern = "insufficient_data"
            if score >= 80:
                concern = "high_concern"
            elif score >= 60:
                concern = "elevated_concern"
            elif score >= 40:
                concern = "moderate_concern"
            else:
                concern = "low_concern"
            contributors = sorted(contributors_by_path.get(stat.rel_path, []))[:8]
            hotspots.append(
                ChangeHotspot(
                    rel_path=stat.rel_path,
                    score=score,
                    concern=concern,
                    factors=tuple(factors),
                    contributors=tuple(contributors),
                )
            )
        return tuple(sorted(hotspots, key=lambda h: h.score, reverse=True)[:256])

    def _ownership(
        self, author_files: Dict[str, Counter], stats: Tuple[GitHistoryStat, ...]
    ) -> Tuple[OwnershipRecord, ...]:
        # Author totals per top-level area; contributors labeled as contributors.
        area_totals: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for author, counter in author_files.items():
            for rel_path, count in counter.items():
                area = rel_path.split("/", 1)[0]
                area_totals[area][author] += count
        records: List[OwnershipRecord] = []
        for area, authors in sorted(area_totals.items()):
            total = sum(authors.values()) or 1
            ranked = sorted(authors.items(), key=lambda item: item[1], reverse=True)
            top_authors = [a for a, _ in ranked[:3]]
            confidence = "reported" if len(ranked) >= 1 else "uncertain"
            records.append(
                OwnershipRecord(
                    area=area or "(root)",
                    path_scope=area,
                    owner=None,
                    kind="recent_contributor" if ranked else "unowned",
                    source="git history (top-level directory)",
                    confidence=confidence,
                    time_range="recent history window",
                    review_required=len(ranked) >= 3,
                    fallback_owner=top_authors[0] if top_authors else None,
                )
            )
        return tuple(records)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
