"""Feature 009 — `.env` files."""

from __future__ import annotations

import pytest

from ferro import ConfigurationError
from ferro.dotenv import load, parse


def test_parsing_rules():
    text = "\n".join(
        [
            "# comment",
            "",
            "FERRO_DATABASE_URL=sqlite:///db.sqlite3",
            "export FERRO_PORT = 9000",
            "QUOTED='single # not a comment'",
            'DOUBLE="line\\nbreak"',
            "TRAILING=value # comment",
            "EMPTY=",
        ]
    )
    assert parse(text) == {
        "FERRO_DATABASE_URL": "sqlite:///db.sqlite3",
        "FERRO_PORT": "9000",
        "QUOTED": "single # not a comment",
        "DOUBLE": "line\nbreak",
        "TRAILING": "value",
        "EMPTY": "",
    }


def test_malformed_lines_name_their_number():
    with pytest.raises(ConfigurationError, match="line 2"):
        parse("A=1\nthis is not a setting\n")
    with pytest.raises(ConfigurationError, match="line 1"):
        parse("1BAD=x")
    with pytest.raises(ConfigurationError, match="line 1"):
        parse("A='unterminated")


def test_environment_wins(tmp_path):
    path = tmp_path / ".env"
    path.write_text("FERRO_A=from-file\nFERRO_B=from-file\n", encoding="utf-8")
    environ = {"FERRO_A": "from-env"}
    loaded = load(path, environ)
    assert environ == {"FERRO_A": "from-env", "FERRO_B": "from-file"}
    assert loaded == ["FERRO_B"]
    assert load(tmp_path / "missing.env", environ) == []
