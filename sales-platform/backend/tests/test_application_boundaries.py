"""Keep application implementation modules independent of the public facade."""
from __future__ import annotations

import ast
from pathlib import Path


APPLICATION = Path(__file__).resolve().parents[1] / "application"


def test_implementation_modules_do_not_import_application_facade():
    for path in APPLICATION.glob("*.py"):
        if path.stem == "service":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module == "service" and node.level == 1), path.name
                assert node.module != "backend.application.service", path.name
            elif isinstance(node, ast.Import):
                assert all(alias.name != "backend.application.service" for alias in node.names), path.name


def test_application_runtime_imports_have_no_cycles():
    modules = {path.stem: path for path in APPLICATION.glob("*.py")}
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
