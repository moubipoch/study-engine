"""Code indexing by file and function: tree-sitter for C/C++, ast for Python, line windows otherwise."""

from __future__ import annotations

import ast
from pathlib import Path

from study.ingest.base import Chunk, Extracted, windows_by_lines

CPP_EXT = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ipp", ".tpp", ".cu"}
PY_EXT = {".py"}
OTHER_CODE_EXT = {".f90", ".f", ".jl", ".m", ".sh", ".cmake", ".ipynb", ".r", ".rs"}
CODE_NAMES = {"Makefile", "CMakeLists.txt", "makefile", "meson.build"}
MAX_FUNC_LINES = 120

_CPP_UNITS = {"function_definition", "class_specifier", "struct_specifier", "union_specifier",
              "enum_specifier", "template_declaration"}
_CPP_CONTAINERS = {"namespace_definition", "linkage_specification", "declaration_list", "translation_unit",
                   "preproc_ifdef", "preproc_if", "preproc_else"}


def is_code_file(path: Path) -> bool:
    return path.suffix.lower() in CPP_EXT | PY_EXT | OTHER_CODE_EXT or path.name in CODE_NAMES


def _cpp_parser():
    import tree_sitter_cpp
    from tree_sitter import Language, Parser
    return Parser(Language(tree_sitter_cpp.language()))


def _node_name(node, src: bytes) -> str:
    """Best-effort human name for a function/class node: its declarator text, trimmed."""
    if node.type == "template_declaration":
        inner = next((c for c in node.named_children if c.type in _CPP_UNITS | {"declaration"}), None)
        return "template " + (_node_name(inner, src) if inner else "")
    decl = node.child_by_field_name("declarator") or node.child_by_field_name("name")
    target = decl if decl is not None else node
    text = src[target.start_byte:target.end_byte].decode(errors="replace")
    if decl is None:
        text = text.split("{", 1)[0]
    kind = {"class_specifier": "class ", "struct_specifier": "struct ", "union_specifier": "union ",
            "enum_specifier": "enum "}.get(node.type, "")
    return (kind + " ".join(text.split()))[:120]


def _cpp_units(root, src: bytes) -> list[tuple[int, int, str]]:
    """(first_line, last_line, name) for functions/classes, descending into namespaces."""
    units = []

    def visit(node):
        for child in node.named_children:
            if child.type in _CPP_UNITS:
                units.append((child.start_point[0] + 1, child.end_point[0] + 1, _node_name(child, src)))
            elif child.type in _CPP_CONTAINERS or child.type.startswith("preproc_if"):
                body = child.child_by_field_name("body")
                visit(body if body is not None else child)

    visit(root)
    return units


def _py_units(text: str) -> list[tuple[int, int, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            kind = "class " if isinstance(node, ast.ClassDef) else "def "
            out.append((first, node.end_lineno or node.lineno, kind + node.name))
    return out


def _chunks_from_units(lines: list[str], units: list[tuple[int, int, str]]) -> list[Chunk]:
    """Units become chunks; uncovered lines (includes, globals, main glue) become 'preamble' chunks."""
    n = len(lines)
    chunks: list[Chunk] = []
    covered = [False] * (n + 2)

    def add(first, last, heading):
        seg = lines[first - 1:last]
        if not "".join(seg).strip():
            return
        if last - first + 1 > MAX_FUNC_LINES:
            for a, b in windows_by_lines(seg, first, size=MAX_FUNC_LINES // 2, overlap=5):
                chunks.append(Chunk(a, b, "\n".join(lines[a - 1:b]), heading))
        else:
            chunks.append(Chunk(first, last, "\n".join(seg), heading))

    for first, last, name in sorted(units):
        if all(covered[first:last + 1]):
            continue  # nested unit already inside an emitted one
        add(first, last, name)
        for i in range(first, last + 1):
            covered[i] = True
    # gaps
    i = 1
    while i <= n:
        if covered[i]:
            i += 1
            continue
        j = i
        while j + 1 <= n and not covered[j + 1]:
            j += 1
        add(i, j, "(file scope)")
        i = j + 1
    chunks.sort(key=lambda c: c.loc_start)
    return chunks


def extract_code(path: Path, display_name: str) -> Extracted:
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    ext = path.suffix.lower()
    if ext in CPP_EXT:
        src = text.encode()
        units = _cpp_units(_cpp_parser().parse(src).root_node, src)
        lang = "cpp"
    elif ext in PY_EXT:
        units, lang = _py_units(text), "python"
    else:
        units, lang = [], ext.lstrip(".") or path.name
    if units:
        chunks = _chunks_from_units(lines, units)
    else:
        chunks = [Chunk(a, b, "\n".join(lines[a - 1:b]), "") for a, b in windows_by_lines(lines, 1)]
    # prefix file name so lexical search hits on file names too
    for c in chunks:
        c.heading = f"{display_name}: {c.heading}".rstrip(": ")
    return Extracted("L", max(len(lines), 1), chunks, [], {"language": lang})
