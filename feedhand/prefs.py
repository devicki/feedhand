"""User prefs in ~/.feedhand/prefs.json (mode 600)."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "auto_approve_bots": [],      # bot names allowed to publish without per-post approval
    "default_mode_x": "api",
    "default_mode_threads": "api",
    "default_mode_instagram": "api",
    "out_dir": "~/feedhand-out",
    "x_link_cost_warning": True,
    "x_premium": False,           # X Premium author: long posts up to 25,000 weighted chars
    "language": "en",
    "media_hosts": ["srv-dev", "srv-bot"],  # SSH aliases `media fetch` may copy from
}


def home() -> Path:
    return Path(os.environ.get("FEEDHAND_HOME", "~/.feedhand")).expanduser()


def path() -> Path:
    return home() / "prefs.json"


def load() -> dict:
    p = path()
    d = dict(DEFAULTS)
    if p.exists():
        d.update(json.loads(p.read_text() or "{}"))
    return d


def _coerce(v: str):
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return v


BOOL_KEYS = {"x_premium", "x_link_cost_warning"}


def _bool(k: str, v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{k} must be true or false, got {v!r}")


def set_many(pairs: list[str]) -> dict:
    d = load()
    for kv in pairs:
        if "=" not in kv:
            raise ValueError(f"expected k=v, got {kv!r}")
        k, v = kv.split("=", 1)
        k, v = k.strip(), _coerce(v.strip())
        d[k] = _bool(k, v) if k in BOOL_KEYS else v
    save(d)
    return d


def save(d: dict) -> None:
    home().mkdir(parents=True, exist_ok=True, mode=0o700)
    p = path()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.chmod(p, 0o600)
