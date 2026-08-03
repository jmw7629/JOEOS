"""Language and framework detection from repository evidence.

Detection is evidence-based: explicit configuration first, then file
extension/metadata, then content heuristics, then conservative inference.
Frameworks are only reported when a manifest or registered entry point makes
them active; an unused dependency never activates a framework.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import FrameworkDetection, LanguageDetection

# language -> file extensions, in priority order
LANGUAGE_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "typescript": (".ts", ".tsx", ".mts", ".cts"),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "svelte": (".svelte",),
    "python": (".py", ".pyi"),
    "swift": (".swift",),
    "objective-c": (".m", ".mm", ".h"),
    "rust": (".rs",),
    "go": (".go",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "c": (".c", ".h"),
    "c++": (".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"),
    "c#": (".cs",),
    "ruby": (".rb",),
    "php": (".php",),
    "sql": (".sql",),
    "shell": (".sh", ".bash", ".zsh", ".ksh"),
    "powershell": (".ps1", ".psm1"),
    "html": (".html", ".htm", ".xhtml"),
    "css": (".css",),
    "scss": (".scss", ".sass"),
    "less": (".less",),
    "json": (".json", ".jsonc"),
    "yaml": (".yaml", ".yml"),
    "toml": (".toml",),
    "ini": (".ini", ".cfg", ".conf"),
    "markdown": (".md", ".markdown", ".mdown"),
    "dockerfile": ("dockerfile", ".dockerfile"),
    "terraform": (".tf", ".tfvars"),
    "nix": (".nix",),
    "protobuf": (".proto",),
    "graphql": (".graphql", ".gql"),
    "xml": (".xml", ".plist", ".xaml"),
    "svg": (".svg",),
    "makefile": ("makefile", "gnumakefile", "cmakelists.txt"),
}

# language -> pattern that must appear in content (case-insensitive)
CONTENT_HINTS: Dict[str, Tuple[str, ...]] = {
    "python": ("def ", "class ", "import ", "from ", "#!/usr/bin/env python"),
    "javascript": ("function ", "const ", "=>", "require(", "from '"),
    "typescript": (": string", ": number", "interface ", "type ", ": void"),
    "shell": ("#!/bin/bash", "#!/usr/bin/env bash", "#!/bin/sh", "#!/bin/zsh"),
    "dockerfile": ("FROM ", "RUN ", "COPY ", "CMD "),
    "terraform": ('"required_providers"', 'provider "', 'resource "', 'variable "'),
    "sql": ("CREATE TABLE", "SELECT ", "INSERT INTO", "ALTER TABLE"),
}

MANIFEST_LANGUAGES: Dict[str, str] = {
    "package.json": "javascript",
    "pnpm-lock.yaml": "javascript",
    "yarn.lock": "javascript",
    "tsconfig.json": "typescript",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "Package.swift": "swift",
    "build.gradle": "java",
    "pom.xml": "java",
    "Gemfile": "ruby",
    "composer.json": "php",
    "*.csproj": "c#",
    "Podfile": "objective-c",
    "mix.exs": "elixir",
    "pubspec.yaml": "dart",
    "stack.yaml": "haskell",
}

MANIFEST_PACKAGE_MANAGER: Dict[str, str] = {
    "package.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "pyproject.toml": "poetry",
    "requirements.txt": "pip",
    "Pipfile": "pipenv",
    "Cargo.toml": "cargo",
    "go.mod": "go modules",
    "Gemfile": "bundler",
    "composer.json": "composer",
    "pubspec.yaml": "pub",
}

FRAMEWORK_MANIFESTS: Tuple[Tuple[str, str, str], ...] = (
    ("sveltekit", "svelte.config.js", "package.json"),
    ("svelte", "svelte", "package.json"),
    ("nextjs", "next.config.js", "package.json"),
    ("vite", "vite.config.js", "package.json"),
    ("react", "react", "package.json"),
    ("vue", "vue", "package.json"),
    ("nuxt", "nuxt.config.ts", "package.json"),
    ("angular", "angular.json", "package.json"),
    ("electron", "electron", "package.json"),
    ("tauri", "tauri.conf.json", "package.json"),
    ("express", "express", "package.json"),
    ("fastify", "fastify", "package.json"),
    ("fastapi", "fastapi", "pyproject.toml"),
    ("flask", "flask", "requirements.txt"),
    ("django", "django", "requirements.txt"),
    ("celery", "celery", "requirements.txt"),
    ("pydantic", "pydantic", "pyproject.toml"),
    ("sqlalchemy", "sqlalchemy", "pyproject.toml"),
    ("react_native", "react-native", "package.json"),
    ("supabase", "supabase", "package.json"),
    ("docker", "docker-compose.yml", "Dockerfile"),
    ("kubernetes", "Chart.yaml", "kustomization.yaml"),
    ("github_actions", ".github/workflows", ".github"),
    ("postgresql", "postgres", "docker-compose.yml"),
    ("sqlite", "sqlite", "requirements.txt"),
    ("redis", "redis", "docker-compose.yml"),
    ("expo", "expo", "package.json"),
)

BUILD_SYSTEM_MANIFESTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("npm scripts", ("package.json",)),
    ("pnpm", ("pnpm-lock.yaml",)),
    ("yarn", ("yarn.lock",)),
    ("poetry", ("pyproject.toml",)),
    ("pip", ("requirements.txt",)),
    ("cargo", ("Cargo.toml",)),
    ("go", ("go.mod",)),
    ("swiftpm", ("Package.swift",)),
    ("gradle", ("build.gradle", "build.gradle.kts")),
    ("maven", ("pom.xml",)),
    ("bundler", ("Gemfile",)),
    ("composer", ("composer.json",)),
    ("make", ("Makefile", "makefile")),
    ("cmake", ("CMakeLists.txt",)),
    ("xcodebuild", ("*.xcodeproj",)),
)

TEST_SYSTEM_MANIFESTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("jest", ("jest.config.js", "jest.config.ts")),
    ("vitest", ("vitest.config.js", "vitest.config.ts")),
    ("pytest", ("pytest.ini", "pyproject.toml")),
    ("unittest", ("test_",)),
    ("go test", ("*_test.go",)),
    ("swift xctest", ("*Tests.swift",)),
    ("cargo test", ("#[cfg(test)]",)),
)


def language_for_file(name: str) -> Optional[str]:
    lower = name.lower()
    for language, extensions in LANGUAGE_EXTENSIONS.items():
        for extension in extensions:
            if lower.endswith(extension):
                return language
    return None


def detect_languages(root: Path, sample: int = 2000) -> Tuple[LanguageDetection, ...]:
    counts: Dict[str, int] = {}
    hints: Dict[str, bool] = {}
    try:
        files = list(root.rglob("*"))
    except OSError:
        files = []
    for path in files:
        if path.is_dir():
            continue
        name = path.name.lower()
        if path.name in MANIFEST_LANGUAGES:
            counts[MANIFEST_LANGUAGES[path.name]] = counts.get(MANIFEST_LANGUAGES[path.name], 0) + 1
            continue
        language = language_for_file(name)
        if language:
            counts[language] = counts.get(language, 0) + 1
            if len(counts) * 3 < sample:
                try:
                    if path.stat().st_size < 100_000:
                        content = path.read_text(encoding="utf-8", errors="ignore")[:4000].lower()
                        for hint_language, patterns in CONTENT_HINTS.items():
                            if any(pattern in content for pattern in patterns):
                                hints[hint_language] = True
                except OSError:
                    pass
    detections: List[LanguageDetection] = []
    total = sum(counts.values()) or 1
    for language, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        share = count / total
        confidence = "reported" if share >= 0.2 else "inferred" if share >= 0.02 else "uncertain"
        if share < 0.01 and language not in hints:
            continue
        evidence = ["%d files with matching extension/manifest" % count]
        if hints.get(language):
            evidence.append("content hints")
        parser_available = language in {"python", "javascript", "typescript", "json", "yaml", "markdown", "shell", "dockerfile"}
        detections.append(
            LanguageDetection(
                language=language,
                confidence=confidence,
                evidence=tuple(evidence),
                parser_available=parser_available,
                symbol_extraction_available=parser_available,
                reference_extraction_available=parser_available,
            )
        )
    if not detections:
        detections.append(LanguageDetection(language="unknown", confidence="uncertain", evidence=("No source evidence found.",)))
    return tuple(detections)


def detect_frameworks(root: Path) -> Tuple[FrameworkDetection, ...]:
    detections: List[FrameworkDetection] = []
    known_files = set()
    try:
        files = list(root.rglob("*"))
    except OSError:
        files = []
    names = set()
    for path in files:
        if path.is_dir():
            continue
        names.add(path.name)
        rel = str(path.relative_to(root))
        parts = rel.split("/")
        if ".github" in parts and "workflows" in parts:
            names.add(".github/workflows")
    for framework, needle, manifest in FRAMEWORK_MANIFESTS:
        matched = False
        if needle in names:
            matched = True
        if not matched and needle in {"package.json"}:
            continue
        if matched:
            detections.append(
                FrameworkDetection(
                    framework=framework,
                    confidence="reported",
                    evidence_files=(needle,),
                    source="manifest",
                    detected_at=_now(),
                )
            )
            continue
        if manifest in names and framework in {"fastapi", "flask", "django", "express", "fastify", "react"}:
            version = _manifest_framework_version(root, framework)
            detections.append(
                FrameworkDetection(
                    framework=framework,
                    version=version,
                    confidence="inferred",
                    evidence_files=(manifest,),
                    source="dependency manifest",
                    detected_at=_now(),
                )
            )
    if not detections and names:
        detections.append(FrameworkDetection(framework="none", confidence="reported", evidence_files=(), source="no manifest evidence", detected_at=_now()))
    return tuple(detections)


def _manifest_framework_version(root: Path, framework: str) -> Optional[str]:
    for manifest_name in ("package.json", "requirements.txt", "pyproject.toml"):
        manifest = root / manifest_name
        if not manifest.exists():
            continue
        try:
            content = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if manifest_name == "package.json":
            import json

            try:
                data = json.loads(content)
            except ValueError:
                return None
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                deps = data.get(section) or {}
                if framework in deps:
                    return str(deps[framework]).lstrip("^~")
        elif manifest_name == "requirements.txt":
            for line in content.splitlines():
                if line.lower().startswith(framework):
                    return line.split("==")[-1].strip() or None
        elif manifest_name == "pyproject.toml":
            if framework in content:
                return None
    return None


def detect_package_manager(root: Path) -> Optional[str]:
    for name in ("pnpm-lock.yaml", "yarn.lock", "package-lock.json"):
        if (root / name).exists():
            return MANIFEST_PACKAGE_MANAGER.get(name.replace("-lock.json", ".json").replace(".lock", ""), "npm")
    for name, manager in MANIFEST_PACKAGE_MANAGER.items():
        if (root / name).exists():
            return manager
    return None


def detect_build_system(root: Path) -> Optional[str]:
    for system, candidates in BUILD_SYSTEM_MANIFESTS:
        for candidate in candidates:
            if candidate.startswith("*") and any(str(p).lower().endswith(candidate[1:]) for p in _iter_names(root)):
                return system
            if (root / candidate).exists():
                return system
    return None


def detect_test_system(root: Path) -> Optional[str]:
    for system, candidates in TEST_SYSTEM_MANIFESTS:
        for candidate in candidates:
            if candidate.startswith("*") and any(p.endswith(candidate[1:]) for p in _iter_names(root)):
                return system
            if (root / candidate).exists():
                return system
    return None


def _iter_names(root: Path) -> Tuple[str, ...]:
    try:
        return tuple(str(path.relative_to(root)) for path in root.rglob("*"))
    except OSError:
        return ()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
