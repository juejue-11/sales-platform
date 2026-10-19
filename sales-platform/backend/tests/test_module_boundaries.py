"""Keep implementation modules independent of their entry points and cycles."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("package,entry", [
    ("application", "service"), ("analysis", "ai"), ("research", "project_research"),
])
def test_implementation_modules_do_not_import_entry_point(package: str, entry: str):
    for path in (BACKEND / package).glob("*.py"):
        if path.stem == entry:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module == entry and node.level == 1), path.name
                assert node.module != f"backend.{package}.{entry}", path.name
                if node.module is None and node.level == 1:
                    assert all(alias.name != entry for alias in node.names), path.name
            elif isinstance(node, ast.Import):
                assert all(alias.name != f"backend.{package}.{entry}" for alias in node.names), path.name


@pytest.mark.parametrize("package", ["application", "analysis", "research", "followup"])
def test_runtime_imports_have_no_cycles(package: str):
    modules = {path.stem: path for path in (BACKEND / package).glob("*.py")}
    dependencies: dict[str, set[str]] = {}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        dependencies[name] = {
            node.module for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in modules
        }

    visited: set[str] = set()

    def visit(name: str, route: tuple[str, ...]) -> None:
        assert name not in route, " -> ".join((*route, name))
        if name in visited:
            return
        for dependency in dependencies[name]:
            visit(dependency, (*route, name))
        visited.add(name)

    for name in modules:
        visit(name, ())
