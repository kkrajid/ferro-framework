"""``.env`` files: ``KEY=VALUE`` lines read by every ``ferro`` command. Variables already set in
the environment win over the file."""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

from ferro.exceptions import ConfigurationError

__all__ = ["load", "parse"]

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), text)


def parse(text: str, source: str = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None:
            raise ConfigurationError(f"{source} line {number}: expected KEY=VALUE, got {raw!r}")
        key, value = match.group(1), match.group(2).strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            while quote == '"' and end > 0 and value[end - 1] == "\\":
                end = value.find(quote, end + 1)
            if end < 0:
                raise ConfigurationError(f"{source} line {number}: unterminated quote")
            inner = value[1:end]
            value = _unescape(inner) if quote == '"' else inner
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def load(path: Path | str = ".env", environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Sets the file's variables that are not in ``environ`` (``os.environ`` by default).
    Returns the names that were set; a missing file sets nothing."""
    path = Path(path)
    environ = os.environ if environ is None else environ
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse(path.read_text(encoding="utf-8"), str(path.name)).items():
        if key not in environ:
            environ[key] = value
            loaded.append(key)
    return loaded
