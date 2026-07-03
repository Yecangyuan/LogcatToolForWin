import ast
from pathlib import Path


def test_project_metadata_allows_python38_builds() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.8"' in pyproject
    assert 'target-version = "py38"' in pyproject


def test_source_avoids_pep604_union_syntax_for_legacy_builds() -> None:
    offenders: list[str] = []

    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and _contains_pep604_union(node.annotation):
                offenders.append(f"{path}:{node.lineno}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ):
                    if arg.annotation is not None and _contains_pep604_union(arg.annotation):
                        offenders.append(f"{path}:{arg.lineno}")
                if node.args.vararg and node.args.vararg.annotation is not None:
                    if _contains_pep604_union(node.args.vararg.annotation):
                        offenders.append(f"{path}:{node.args.vararg.lineno}")
                if node.args.kwarg and node.args.kwarg.annotation is not None:
                    if _contains_pep604_union(node.args.kwarg.annotation):
                        offenders.append(f"{path}:{node.args.kwarg.lineno}")
                if node.returns is not None and _contains_pep604_union(node.returns):
                    offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_source_avoids_dataclass_slots_for_legacy_builds() -> None:
    offenders: list[str] = []

    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for decorator in node.decorator_list:
                if _is_dataclass_with_slots(decorator):
                    offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def _contains_pep604_union(node: ast.AST) -> bool:
    return any(isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr) for child in ast.walk(node))


def _is_dataclass_with_slots(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        is_dataclass = node.func.id == "dataclass"
    elif isinstance(node.func, ast.Attribute):
        is_dataclass = node.func.attr == "dataclass"
    else:
        is_dataclass = False
    return is_dataclass and any(keyword.arg == "slots" for keyword in node.keywords)
