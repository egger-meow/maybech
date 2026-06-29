import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
ENV_HELPERS = {"_get", "_get_float", "_get_int"}
SENSITIVE_KEYS = {
    "OKX_API_KEY",
    "OKX_API_SECRET",
    "OKX_PASSPHRASE",
    "DEMO_OKX_API_KEY",
    "DEMO_OKX_API_SECRET",
    "DEMO_OKX_PASSPHRASE",
    "LINE_CHANNEL_ACCESS_TOKEN",
    "LINE_CHANNEL_SECRET",
    "LINE_USER_ID",
    "EMAIL_SENDER",
    "EMAIL_PASSWORD",
    "EMAIL_RECEIVER",
}


def _env_example_values() -> dict[str, str]:
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _python_env_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function_name = ""
            if isinstance(node.func, ast.Name):
                function_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                function_name = node.func.attr
            first_arg = node.args[0]
            if function_name in ENV_HELPERS | {"getenv"} and isinstance(
                first_arg, ast.Constant
            ) and isinstance(first_arg.value, str):
                keys.add(first_arg.value)
    return keys


def _frontend_env_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "frontend").rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        if {"node_modules", "generated", ".next"} & set(path.parts):
            continue
        content = path.read_text(encoding="utf-8")
        keys.update(re.findall(r"process\.env\.([A-Z][A-Z0-9_]*)", content))
    return keys


def test_env_example_exactly_covers_runtime_env_vars():
    values = _env_example_values()
    runtime_keys = _python_env_keys() | _frontend_env_keys()

    assert set(values) == runtime_keys


def test_env_example_has_no_duplicate_keys_or_private_values():
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    keys = [line.split("=", 1)[0] for line in lines if line and not line.startswith("#")]
    values = _env_example_values()

    assert len(keys) == len(set(keys))
    assert all(values[key] == "" for key in SENSITIVE_KEYS)
