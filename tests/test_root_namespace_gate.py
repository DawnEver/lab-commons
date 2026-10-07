import ast
import subprocess
import sys
from pathlib import Path


def test_root_namespace_has_no_eager_imports_or_reflective_facade():
    source = Path(__file__).parents[1] / 'src' / 'lab_commons' / '__init__.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)) for node in ast.walk(tree))


def test_stdlib_logging_does_not_load_optional_capabilities():
    probe = subprocess.run(
        [sys.executable, '-c',
         "import sys; from lab_commons.log import get_logger; "
         "assert not {'pint', 'pydantic', 'structlog', 'platformdirs', 'rtoml', 'numpy'} & set(sys.modules)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
