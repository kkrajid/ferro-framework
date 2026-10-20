import os

import pytest

from ferro import ConfigurationError
from ferro.config import resolve


def test_defaults_for_run_mode():
    settings = resolve(mode="run")
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.workers == (os.cpu_count() or 1)
    assert settings.debug is False
    assert settings.max_body_size == 1024 * 1024
    assert settings.request_timeout == 30.0
    assert settings.shutdown_timeout == 30.0
    assert settings.header_timeout == 5.0
    assert settings.max_connections == 10_000
    assert settings.sync_threads == 40
    assert settings.log_level == "info"
    assert set(settings.sources.values()) == {"default"}


def test_dev_mode_defaults_to_debug():
    settings = resolve(mode="dev")
    assert settings.debug is True
    assert settings.sources["debug"] == "default"


def test_precedence_cli_over_env_over_app_over_default():
    settings = resolve(
        cli={"port": 9000},
        env={"FERRO_PORT": "9001", "FERRO_HOST": "0.0.0.0", "FERRO_WORKERS": "3"},
        app={"port": 9002, "host": "10.0.0.1", "workers": 7, "request_timeout": 5},
        mode="run",
    )
    assert settings.port == 9000
    assert settings.sources["port"] == "cli"
    assert settings.host == "0.0.0.0"
    assert settings.sources["host"] == "env (FERRO_HOST)"
    assert settings.workers == 3
    assert settings.request_timeout == 5.0
    assert settings.sources["request_timeout"] == "App(request_timeout=...)"
    assert settings.max_body_size == 1024 * 1024
    assert settings.sources["max_body_size"] == "default"


def test_none_values_are_ignored():
    settings = resolve(cli={"port": None}, app={"port": None}, mode="run")
    assert settings.port == 8000


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("off", False)],
)
def test_bool_parsing_from_env(raw, expected):
    assert resolve(env={"FERRO_DEBUG": raw}, mode="dev").debug is expected


@pytest.mark.parametrize(
    ("kwargs", "setting", "source"),
    [
        ({"env": {"FERRO_WORKERS": "abc"}}, "workers", "FERRO_WORKERS"),
        ({"env": {"FERRO_DEBUG": "maybe"}}, "debug", "FERRO_DEBUG"),
        ({"app": {"port": -1}}, "port", "App(port=...)"),
        ({"app": {"port": 70000}}, "port", "App(port=...)"),
        ({"cli": {"workers": 0}}, "workers", "cli"),
        ({"cli": {"log_level": "verbose"}}, "log_level", "cli"),
        ({"cli": {"request_timeout": 0.01}}, "request_timeout", "cli"),
        ({"cli": {"max_body_size": 0}}, "max_body_size", "cli"),
        ({"cli": {"sync_threads": 0}}, "sync_threads", "cli"),
        ({"cli": {"processes": 0}}, "processes", "cli"),
        ({"env": {"FERRO_PROCESSES": "65"}}, "processes", "FERRO_PROCESSES"),
        ({"cli": {"host": ""}}, "host", "cli"),
    ],
)
def test_invalid_values_name_setting_and_source(kwargs, setting, source):
    with pytest.raises(ConfigurationError) as info:
        resolve(mode="run", **kwargs)
    message = str(info.value)
    assert setting in message
    assert source in message


def test_unknown_app_setting_is_rejected():
    with pytest.raises(ConfigurationError, match="unknown setting 'prot'"):
        resolve(app={"prot": 80}, mode="run")


def test_native_settings_shape():
    native = resolve(cli={"request_timeout": 2}, mode="run").to_native()
    assert native["request_timeout"] == 2.0
    assert native["log_level"] == "info"
    assert "sync_threads" not in native
    assert "processes" not in native
    assert set(native) == {
        "host",
        "port",
        "workers",
        "debug",
        "max_body_size",
        "request_timeout",
        "shutdown_timeout",
        "header_timeout",
        "max_connections",
        "log_level",
        # Feature 006
        "csrf",
        "trusted_origins",
        "trusted_proxies",
        "rate_limit",
        "cors",
        "hsts",
        "hsts_max_age",
        "content_security_policy",
        # Feature 023
        "hsts_include_subdomains",
        "hsts_preload",
        "allowed_hosts",
        "https_redirect",
        # Feature 011
        "health_path",
        "ready_path",
        "metrics",
        "metrics_path",
        "metrics_allow",
        # Feature 014
        "tls_cert",
        "tls_key",
    }
    assert native["csrf"] is True and native["trusted_origins"] == []


# Feature 011: probes, metrics, log format, drain.


def test_production_hardening_defaults():
    settings = resolve(mode="run", env={})
    assert settings.health_path == "/healthz"
    assert settings.ready_path == "/readyz"
    assert settings.metrics is False
    assert settings.metrics_path == "/metrics"
    assert settings.metrics_allow == ("127.0.0.1", "::1")
    assert settings.log_format == "text"
    assert settings.shutdown_drain == 0.0


def test_production_hardening_precedence():
    settings = resolve(
        cli={"log_format": "json", "shutdown_drain": 2},
        env={
            "FERRO_METRICS": "1",
            "FERRO_METRICS_ALLOW": "10.0.0.0/8, 127.0.0.1",
            "FERRO_HEALTH_PATH": "",
        },
        app={"ready_path": "/ready", "log_format": "text"},
        mode="run",
    )
    assert settings.log_format == "json"
    assert settings.shutdown_drain == 2.0
    assert settings.metrics is True
    assert settings.metrics_allow == ("10.0.0.0/8", "127.0.0.1")
    assert settings.health_path == "", "an empty path disables the probe"
    assert settings.ready_path == "/ready"


@pytest.mark.parametrize(
    ("kwargs", "setting", "source"),
    [
        ({"env": {"FERRO_HEALTH_PATH": "healthz"}}, "health_path", "FERRO_HEALTH_PATH"),
        ({"app": {"ready_path": "/a b"}}, "ready_path", "App(ready_path=...)"),
        ({"cli": {"metrics_path": "/m/{x}"}}, "metrics_path", "cli"),
        ({"env": {"FERRO_METRICS_ALLOW": "not-an-ip"}}, "metrics_allow", "FERRO_METRICS_ALLOW"),
        ({"cli": {"log_format": "xml"}}, "log_format", "cli"),
        ({"cli": {"shutdown_drain": -1}}, "shutdown_drain", "cli"),
        ({"cli": {"shutdown_drain": 3601}}, "shutdown_drain", "cli"),
    ],
)
def test_invalid_hardening_settings_name_setting_and_source(kwargs, setting, source):
    with pytest.raises(ConfigurationError) as info:
        resolve(mode="run", **{"env": {}, **kwargs})
    assert setting in str(info.value)
    assert source in str(info.value)


def test_probe_and_metrics_paths_must_differ():
    with pytest.raises(ConfigurationError, match="ready_path"):
        resolve(app={"health_path": "/same", "ready_path": "/same/"}, env={}, mode="run")
    with pytest.raises(ConfigurationError, match="metrics_path"):
        resolve(app={"metrics": True, "metrics_path": "/healthz"}, env={}, mode="run")
    # Disabled probes and an unused metrics path are not compared.
    resolve(app={"health_path": "", "ready_path": ""}, env={}, mode="run")
    resolve(app={"metrics_path": "/healthz"}, env={}, mode="run")


def test_hardening_settings_reach_the_engine():
    native = resolve(app={"metrics": True}, env={}, mode="run").to_native()
    assert native["health_path"] == "/healthz"
    assert native["ready_path"] == "/readyz"
    assert native["metrics"] is True
    assert native["metrics_path"] == "/metrics"
    assert native["metrics_allow"] == ["127.0.0.1", "::1"]
    assert "shutdown_drain" not in native
    assert "log_format" not in native


def test_cli_accepts_log_format_and_drain():
    from ferro.cli import _cli_settings, _parser

    args = _parser().parse_args(["run", "--log-format", "json", "--shutdown-drain", "2.5"])
    values = _cli_settings(args)
    assert values["log_format"] == "json"
    assert values["shutdown_drain"] == 2.5


# Feature 014: TLS.


def test_tls_settings_come_in_pairs():
    settings = resolve(
        cli={"tls_cert": "cert.pem"}, env={"FERRO_TLS_KEY": "key.pem"}, app={}, mode="run"
    )
    assert (settings.tls_cert, settings.tls_key, settings.scheme) == (
        "cert.pem",
        "key.pem",
        "https",
    )
    assert resolve(env={}, mode="run").scheme == "http"
    with pytest.raises(ConfigurationError, match=r"'tls_cert' \(from cli\) needs 'tls_key'"):
        resolve(cli={"tls_cert": "cert.pem"}, env={}, mode="run")
    with pytest.raises(ConfigurationError, match=r"'tls_key' .* needs 'tls_cert'"):
        resolve(env={}, app={"tls_key": "key.pem"}, mode="run")


def test_cli_accepts_tls_files():
    from ferro.cli import _cli_settings, _parser

    args = _parser().parse_args(["run", "--tls-cert", "c.pem", "--tls-key", "k.pem"])
    values = _cli_settings(args)
    assert (values["tls_cert"], values["tls_key"]) == ("c.pem", "k.pem")
