"""Bounded, language-aware parser adapters with per-file failure isolation.

No external parsing libraries are required. Extraction is conservative and
evidence-based: symbols and references are recovered via bounded lexical
heuristics scoped per language. Anything ambiguous is skipped rather than
guessed. Each parse returns `(symbols, references, error)`; failures never
abort a scan.

The parser registry maps language -> adapter and enforces output limits so a
single pathological file cannot unboundedly grow the index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .models import ReferenceKind, ReferenceRecord, ResolutionState, SymbolKind, SymbolRecord

MAX_SYMBOLS_PER_FILE = 1200
MAX_REFERENCES_PER_FILE = 4000
MAX_LINES = 120_000
MAX_LINE_LENGTH = 4096

_PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import\s+[\w*]+(?:\s+as\s+\w+)?|import\s+([\w.]+))"
)
_PY_DEF = re.compile(r"^\s*(?:async\s+def|def)\s+([A-Za-z_][\w]*)\s*\(")
_PY_CLASS = re.compile(r"^\s*class\s+([A-Za-z_][\w]*)")
_PY_DECORATOR = re.compile(r"^\s*@([\w.]+)")
_JS_IMPORT = re.compile(
    r"^\s*import\s+(?:[^'\"]+\s+from\s+)?['\"]([@\w.\-/]+)['\"]"
)
_JS_EXPORT = re.compile(r"^\s*export\s+(?:default\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)")
_JS_FUNCTION = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("
)
_JS_CLASS = re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")
_JS_CONST = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")
_JS_REQUIRE = re.compile(r"^\s*(?:const|let|var|)\s*\w+\s*=\s*require\(['\"]([^'\"]+)['\"]\)")
_TS_TYPE = re.compile(r"^(?:export\s+)?(?:type|interface)\s+([A-Za-z_$][\w$]*)")
_TS_ENUM = re.compile(r"^(?:export\s+)?enum\s+([A-Za-z_$][\w$]*)")
_GO_IMPORT = re.compile(r'^\s*import\s+("[\w./\-]+"|[a-z]+\s+"[\w./\-]+")')
_GO_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s+)?([A-Z][A-Za-z0-9]*)\s*\(")
_RUST_USE = re.compile(r"^use\s+([\w:]+)")
_RUST_FN = re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][\w]*)")
_RUST_STRUCT = re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_][\w]*)")
_JAVA_CLASS = re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:final\s+)?class\s+([A-Z][\w]*)")
_JAVA_METHOD = re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?[\w<>,\[\]\s]+\s+([a-z][\w]*)\s*\(")
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\.\w+")
_SQL_CREATE = re.compile(r"^\s*create\s+(?:table|index|view)\s+(?:if\s+not\s+exists\s+)?([`\"\[\]a-z0-9_.]+)", re.IGNORECASE)
_SQL_ALTER = re.compile(r"^\s*alter\s+table\s+([`\"\[\]a-z0-9_.]+)", re.IGNORECASE)
_SSH = re.compile(r"^\s*([a-zA-Z_][\w]*)\s*\(\)\s*\{")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)")
_MD_CODE = re.compile(r"^```")
_SCRIPT_OPTION = re.compile(r"^\s*([a-zA-Z_][\w-]*)\s*=\s*\(")
_SH_FUNCTION = re.compile(r"^\s*([a-zA-Z_][\w-]*)\s*\(\)\s*\{")
_SH_INCLUDE = re.compile(r"^\s*\.\s+([\w./\-]+)|\bsource\s+([\w./\-]+)")
_ANNOTATION = re.compile(r"^@([\w.]+)")
_ROUTER = re.compile(r"\.(?:get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]")
_ENV_KEY = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=")
_DOCKER_FROM = re.compile(r"^\s*FROM\s+([\w.:\-]+)(?:\s+AS\s+([\w\-]+))?\s*$", re.IGNORECASE)
_DOCKER_COPY = re.compile(r"^\s*COPY\s+([^\s]+)", re.IGNORECASE)


@dataclass
class ParseResult:
    symbols: List[SymbolRecord] = field(default_factory=list)
    references: List[ReferenceRecord] = field(default_factory=list)
    error: Optional[str] = None


ParserAdapter = Callable[[str, str, str, str, int], ParseResult]


def _slice_lines(content: str) -> List[str]:
    lines = content.splitlines()[:MAX_LINES]
    return [line[:MAX_LINE_LENGTH] for line in lines]


def _parse_python(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    module_parts: List[str] = []
    current_class: Optional[Tuple[str, int]] = None
    in_class_scope = False
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        import_match = _PY_IMPORT.match(raw)
        if import_match:
            module = import_match.group(1) or import_match.group(2)
            result.references.append(
                _reference(project_id, file_id, rel_path, module, line_number,
                           "import", "python", "inferred")
            )
            continue
        decorator_match = _PY_DECORATOR.match(raw)
        if decorator_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, decorator_match.group(1),
                        "decorator", "python", line_number, line_number,
                        "unknown", False, "python")
            )
            continue
        class_match = _PY_CLASS.match(raw)
        if class_match:
            name = class_match.group(1)
            module_parts.append(name)
            current_class = (name, line_number)
            in_class_scope = True
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, name, "class", "python",
                        line_number, line_number, "public", True, "python",
                        parent_symbol=None, module=".".join(module_parts[:-1]) if module_parts else "",
                        signature=stripped)
            )
            continue
        def_match = _PY_DEF.match(raw)
        if def_match:
            name = def_match.group(1)
            parent = None
            if in_class_scope and current_class:
                parent = current_class[0]
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, name,
                        "method" if parent else "function", "python",
                        line_number, line_number, "public", True, "python",
                        parent_symbol=parent, module=".".join(module_parts),
                        signature=stripped)
            )
            continue
    return result


def _parse_javascript(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped:
            continue
        import_match = _JS_IMPORT.match(raw)
        if import_match:
            result.references.append(
                _reference(project_id, file_id, rel_path, import_match.group(1),
                           line_number, "import", "javascript", "inferred")
            )
            continue
        require_match = _JS_REQUIRE.match(raw)
        if require_match:
            result.references.append(
                _reference(project_id, file_id, rel_path, require_match.group(1),
                           line_number, "import", "javascript", "inferred")
            )
            continue
        export_match = _JS_EXPORT.match(raw)
        if export_match:
            name = export_match.group(1)
            kind = "class" if "class " in raw else "function" if "function" in raw else "variable"
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, name, kind, "javascript",
                        line_number, line_number, "public", True, "javascript",
                        signature=stripped)
            )
            continue
        class_match = _JS_CLASS.match(raw)
        if class_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, class_match.group(1),
                        "class", "javascript", line_number, line_number,
                        "public", True, "javascript", signature=stripped)
            )
            continue
        func_match = _JS_FUNCTION.match(raw)
        if func_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, func_match.group(1),
                        "function", "javascript", line_number, line_number,
                        "public", True, "javascript", signature=stripped)
            )
            continue
        const_match = _JS_CONST.match(raw)
        if const_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, const_match.group(1),
                        "constant", "javascript", line_number, line_number,
                        "public", True, "javascript", signature=stripped)
            )
            continue
        route_match = _ROUTER.search(raw)
        if route_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, route_match.group(1),
                        "route", "javascript", line_number, line_number,
                        "public", True, "javascript", signature=stripped)
            )
            continue
    return result


def _parse_typescript(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = _parse_javascript(content, project_id, file_id, rel_path, start_line)
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped:
            continue
        type_match = _TS_TYPE.match(raw)
        if type_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, type_match.group(1),
                        "type", "typescript", line_number, line_number,
                        "public", True, "typescript", signature=stripped)
            )
            continue
        enum_match = _TS_ENUM.match(raw)
        if enum_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, enum_match.group(1),
                        "enum", "typescript", line_number, line_number,
                        "public", True, "typescript", signature=stripped)
            )
    return result


def _parse_go(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped:
            continue
        import_match = _GO_IMPORT.match(raw)
        if import_match:
            target = re.search(r'"([\w./\-]+)"', import_match.group(1))
            if target:
                result.references.append(
                    _reference(project_id, file_id, rel_path, target.group(1),
                               line_number, "import", "go", "inferred")
                )
            continue
        func_match = _GO_FUNC.match(raw)
        if func_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, func_match.group(1),
                        "function", "go", line_number, line_number,
                        "public", True, "go", signature=stripped)
            )
    return result


def _parse_rust(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped:
            continue
        use_match = _RUST_USE.match(raw)
        if use_match:
            result.references.append(
                _reference(project_id, file_id, rel_path, use_match.group(1),
                           line_number, "import", "rust", "inferred")
            )
            continue
        fn_match = _RUST_FN.match(raw)
        if fn_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, fn_match.group(1),
                        "function", "rust", line_number, line_number,
                        "public", True, "rust", signature=stripped)
            )
            continue
        struct_match = _RUST_STRUCT.match(raw)
        if struct_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, struct_match.group(1),
                        "struct", "rust", line_number, line_number,
                        "public", True, "rust", signature=stripped)
            )
    return result


def _parse_java(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped:
            continue
        import_match = _JAVA_IMPORT.match(raw)
        if import_match:
            result.references.append(
                _reference(project_id, file_id, rel_path, import_match.group(1),
                           line_number, "import", "java", "inferred")
            )
            continue
        class_match = _JAVA_CLASS.match(raw)
        if class_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, class_match.group(1),
                        "class", "java", line_number, line_number,
                        "public", True, "java", signature=stripped)
            )
            continue
        method_match = _JAVA_METHOD.match(raw)
        if method_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, method_match.group(1),
                        "method", "java", line_number, line_number,
                        "public", True, "java", signature=stripped)
            )
    return result


def _parse_sql(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("--"):
            continue
        create_match = _SQL_CREATE.match(raw)
        if create_match:
            name = create_match.group(1).strip("`\"[]")
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, name, "table", "sql",
                        line_number, line_number, "unknown", False, "sql",
                        signature=stripped)
            )
            continue
        alter_match = _SQL_ALTER.match(raw)
        if alter_match:
            name = alter_match.group(1).strip("`\"[]")
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, name, "table", "sql",
                        line_number, line_number, "unknown", False, "sql",
                        signature=stripped)
            )
    return result


def _parse_markdown(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    in_code = False
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped:
            continue
        if _MD_CODE.match(raw):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading_match = _MD_HEADING.match(raw)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, title, "section", "markdown",
                        line_number, line_number, "unknown", False, "markdown",
                        signature=("#" * level) + " " + title)
            )
    return result


def _parse_shell(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        include_match = _SH_INCLUDE.match(raw)
        if include_match:
            target = include_match.group(1) or include_match.group(2)
            result.references.append(
                _reference(project_id, file_id, rel_path, target, line_number,
                           "import", "shell", "inferred")
            )
            continue
        func_match = _SH_FUNCTION.match(raw)
        if func_match:
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, func_match.group(1),
                        "function", "shell", line_number, line_number,
                        "public", True, "shell", signature=stripped)
            )
            continue
    return result


def _parse_dockerfile(content: str, project_id: str, file_id: str, rel_path: str, start_line: int) -> ParseResult:
    result = ParseResult()
    lines = _slice_lines(content)
    for index, raw in enumerate(lines):
        line_number = index + 1 + start_line - 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        from_match = _DOCKER_FROM.match(raw)
        if from_match:
            name = from_match.group(2) or from_match.group(1)
            result.symbols.append(
                _symbol(project_id, file_id, rel_path, name, "task", "dockerfile",
                        line_number, line_number, "public", False, "dockerfile",
                        signature=stripped)
            )
            continue
        copy_match = _DOCKER_COPY.match(raw)
        if copy_match:
            result.references.append(
                _reference(project_id, file_id, rel_path, copy_match.group(1),
                           line_number, "dependency", "dockerfile", "inferred")
            )
    return result


ADAPTERS: Dict[str, ParserAdapter] = {
    "python": _parse_python,
    "javascript": _parse_javascript,
    "typescript": _parse_typescript,
    "go": _parse_go,
    "rust": _parse_rust,
    "java": _parse_java,
    "sql": _parse_sql,
    "markdown": _parse_markdown,
    "shell": _parse_shell,
    "dockerfile": _parse_dockerfile,
}

SUPPORTED_LANGUAGES = frozenset(ADAPTERS.keys())


def supported_languages() -> Tuple[str, ...]:
    return tuple(sorted(SUPPORTED_LANGUAGES))


def parse(content: str, language: str, project_id: str, file_id: str, rel_path: str) -> ParseResult:
    """Parse a single file. Never raises; failures are isolated."""
    adapter = ADAPTERS.get(language)
    if adapter is None:
        return ParseResult(error="no_parser")
    try:
        result = adapter(content, project_id, file_id, rel_path, 1)
    except Exception as exc:  # pragma: no cover - defensive isolation
        return ParseResult(error="parser_failure:%s" % exc.__class__.__name__)
    if len(result.symbols) > MAX_SYMBOLS_PER_FILE:
        result.symbols = result.symbols[:MAX_SYMBOLS_PER_FILE]
        result.error = "symbol_limit"
    if len(result.references) > MAX_REFERENCES_PER_FILE:
        result.references = result.references[:MAX_REFERENCES_PER_FILE]
        result.error = "reference_limit"
    return result


def _symbol(
    project_id: str, file_id: str, rel_path: str, name: str, kind: str,
    language: str, line: int, end_line: int, visibility: str,
    exported: bool, parser: str, *, parent_symbol: Optional[str] = None,
    module: str = "", signature: str = "",
) -> SymbolRecord:
    qualified = (".".join(filter(None, [module, parent_symbol, name])) if language == "python"
                 else name)
    symbol_id = _stable(project_id, file_id, kind, name, line)
    return SymbolRecord(
        symbol_id=symbol_id,
        project_id=project_id,
        file_id=file_id,
        name=name,
        qualified_name=qualified,
        kind=kind,
        language=language,
        line=line,
        end_line=end_line,
        visibility=visibility,
        exported=exported,
        signature=signature,
        parent_symbol=parent_symbol,
        module=module,
        parser=parser,
        confidence="reported" if language not in {"javascript", "typescript"} else "inferred",
        content_version=_hash_of("%s:%s:%s" % (project_id, file_id, rel_path)),
    )


def _reference(
    project_id: str, file_id: str, rel_path: str, target_text: str,
    line: int, kind: str, parser: str, resolution: str,
) -> ReferenceRecord:
    reference_id = _stable("ref", project_id, file_id, target_text, line, kind)
    return ReferenceRecord(
        reference_id=reference_id,
        project_id=project_id,
        source_symbol_id=None,
        target_symbol_id=None,
        source_file_id=file_id,
        target_file_id=None,
        rel_path=rel_path,
        target_text=target_text,
        kind=kind,
        line=line,
        resolution=resolution,
        parser=parser,
        confidence="inferred",
    )


def _stable(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _hash_of(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
