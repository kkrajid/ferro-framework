"""Feature 017: the public API frozen for 0.5.x (docs/stability.md) and the deployment files'
consistency with the real settings and commands."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from ferro.cli import _parser
from ferro.config import SETTING_NAMES, env_name

ROOT = Path(__file__).resolve().parents[2]

PUBLIC = {
    "ferro": [
        "App", "BackgroundTasks", "CORS", "ConfigurationError", "Cookie", "Depends",
        "EventStream", "FileResponse", "Form", "FormData", "HTMLResponse", "HTTPException",
        "Header", "JSONResponse", "PlainTextResponse", "RedirectResponse", "Request", "Response",
        "ServerSentEvent", "StreamingResponse", "UploadFile", "ValidationError", "WebSocket",
        "WebSocketDisconnect", "__version__",
    ],
    "ferro.templating": ["Templates"],
    "ferro.models": [
        "Abs", "Avg", "BigInteger", "Boolean", "CASCADE", "Case", "Coalesce", "Concat", "Count",
        "DO_NOTHING", "DatabaseError", "DatabaseUnavailableError", "Date", "DateTime", "Decimal",
        "Exists", "F", "Field", "FieldError", "Float", "ForeignKey", "Func", "Greatest",
        "Integer", "IntegrityError", "JSON", "Least", "Length", "Lower", "ManyToMany", "Max",
        "Min", "Model", "MultipleObjectsReturned", "Now", "ObjectDoesNotExist", "OneToOne",
        "OperationalError", "OuterRef", "PROTECT", "ProtectedError", "Q", "QuerySet",
        "RelatedNotLoaded", "SET_NULL", "String", "Subquery", "Sum", "Text", "Time",
        "TransactionError", "Trim", "UUID", "Upper", "Value", "When", "prefetch_related_objects",
    ],
    "ferro.db": [
        "DatabaseUnavailableError", "TransactionError", "connect", "create_tables", "disconnect",
        "execute", "fetch", "is_connected", "on_commit", "request_transaction",
        "reset_sequences", "transaction",
    ],
    "ferro.jobs": ["Job", "Task", "enqueue", "retry", "run_worker", "task"],
    "ferro.security": [
        "APIKeyCookie", "APIKeyHeader", "APIKeyQuery", "CORS", "HTTPBasic", "HTTPBearer",
        "SecurityScheme",
    ],
    "ferro.auth": [
        "AnonymousUser", "Group", "LoginThrottled", "PasswordError", "Permission", "Session",
        "User", "auth_middleware", "authenticate", "create_permissions", "login",
        "login_required", "logout", "make_password", "permission_required", "staff_required",
        "validate_password",
    ],
    "ferro.admin": ["AdminSite", "ModelAdmin", "admin"],
    "ferro.signals": [
        "Signal", "got_request_exception", "m2m_changed", "post_delete", "post_migrate",
        "post_save", "pre_delete", "pre_save", "receiver", "request_finished", "request_started",
        "user_logged_in", "user_logged_out", "user_login_failed",
    ],
    "ferro.cache": [
        "BaseCache", "DEFAULT_TIMEOUT", "DatabaseCache", "MemoryCache", "cache", "configure",
    ],
    "ferro.migrations": ["Migration", "MigrationError", "ops"],
    "ferro.testing": [
        "TestClient", "TestResponse", "TestStreamResponse", "TestWebSocket",
        "WebSocketDisconnect", "WebSocketRejected", "connect_websocket",
    ],
    "ferro.websockets": ["WebSocket", "WebSocketDisconnect"],
    "ferro.broadcast": [
        "MAX_MESSAGE_BYTES", "Broadcast", "Subscription", "broadcast", "configure",
    ],
}  # fmt: skip


@pytest.mark.parametrize("module", sorted(PUBLIC))
def test_public_names_are_frozen(module):
    imported = importlib.import_module(module)
    assert sorted(imported.__all__) == sorted(PUBLIC[module]), (
        f"{module}.__all__ changed: update docs/stability.md and the changelog (0.5.x may only "
        "add names)"
    )
    for name in imported.__all__:
        assert hasattr(imported, name), f"{module}.{name} is exported but missing"


def test_stability_policy_lists_every_module():
    policy = (ROOT / "docs" / "stability.md").read_text(encoding="utf-8")
    for module in PUBLIC:
        assert f"`{module}`" in policy, module


def deploy_texts() -> dict[str, str]:
    """The deployment files and every user-facing document."""
    docs = ROOT / "docs"
    paths = [
        *(ROOT / "deploy").iterdir(),
        docs / "deployment.md",
        docs / "getting-started.md",
        *(docs / "guide").glob("*.md"),
    ]
    return {path.name: path.read_text(encoding="utf-8") for path in paths if path.is_file()}


def test_deployment_files_use_real_settings():
    known = {env_name(name) for name in SETTING_NAMES} | {"FERRO_SUPERUSER_PASSWORD"}
    for name, text in deploy_texts().items():
        for variable in set(re.findall(r"FERRO_[A-Z_]+", text)):
            assert variable in known, f"{name}: unknown setting {variable}"


def test_deployment_files_use_real_commands_and_options():
    commands = _parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    run_options = {
        option for action in commands["run"]._actions for option in action.option_strings
    }
    for name, text in deploy_texts().items():
        # `sudo -u ferro ...` and `useradd ... ferro` name the system user, `from ferro import`
        # the package — not the command.
        pattern = r"(?<!-u )(?<!from )(?<!import )(?<![\w.-])ferro (?!run\b)([a-z][a-z-]+)"
        for command in re.findall(pattern, text):
            assert command in commands, f"{name}: unknown command ferro {command}"
        for line in re.findall(r"ferro\"?,? \"?run\b[^\n]*", text):
            for option in re.findall(r"--[a-z][a-z-]+", line):
                assert option in run_options, f"{name}: unknown option {option} in {line!r}"
        for path in re.findall(r"/(?:healthz|readyz|metrics)\b", text):
            assert path in ("/healthz", "/readyz", "/metrics")
