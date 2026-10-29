"""Validated TOML configuration with environment substitution."""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


@dataclass(frozen=True)
class Library:
    name: str
    kind: str
    path: Path
    plex_section: str = ""
    plex_path: str = ""
    sonarr_path: str = ""
    season_numbering: bool = False


@dataclass(frozen=True)
class Config:
    libraries: tuple[Library, ...]
    state: Path
    tmdb_token: str = ""
    plex_url: str = ""
    plex_token: str = ""
    webhook_token: str = ""
    language: str = "de-DE"
    fallback_language: str = "en-US"
    dry_run: bool = True
    replace_existing: bool = False
    poll_seconds: int = 60
    polling_enabled: bool = True
    stable_seconds: int = 30
    port: int = 8080
    cache_seconds: int = 86400
    retry_seconds: int = 60


def path_suffix(value: str, prefix: str) -> str | None:
    """Boundary-aware mapping; Windows input is case insensitive."""
    value, prefix = value.replace("\\", "/"), prefix.replace("\\", "/").rstrip("/")
    if not prefix:
        return None
    a, b = (value.casefold(), prefix.casefold()) if re.match(r"^[A-Za-z]:", prefix) else (value, prefix)
    if a == b:
        return ""
    if a.startswith(b + "/"):
        suffix = value[len(prefix) + 1:]
        if any(part in (".", "..") for part in suffix.split("/")):
            raise ValueError("Unsicherer relativer Pfad")
        return suffix
    return None


def contained(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def load(path: Path) -> Config:
    def expand(value):
        if isinstance(value, str):
            def replace(match):
                key = match.group(1)
                if key not in os.environ:
                    raise ValueError(f"Umgebungsvariable fehlt: {key}")
                return os.environ[key]
            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    data = expand(tomllib.loads(path.read_text(encoding="utf-8")))
    settings = data.get("settings", {})
    for key in ("tmdb_token", "plex_url", "plex_token", "webhook_token", "state"):
        env = "METADACHS_" + key.upper()
        if env in os.environ:
            settings[key] = os.environ[env]
    for key in ("dry_run", "replace_existing", "polling_enabled"):
        env = "METADACHS_" + key.upper()
        if env in os.environ:
            if os.environ[env].lower() not in ("true", "false"):
                raise ValueError(f"{env} muss true oder false sein")
            settings[key] = os.environ[env].lower() == "true"
    libs = []
    for raw in data.get("libraries", []):
        item = Library(**{**raw, "path": Path(raw["path"])})
        if item.kind not in ("movie", "tv") or not item.name or not item.path.is_absolute():
            raise ValueError("Mediathek benötigt name, kind=movie/tv und absoluten path")
        if item.plex_section and not item.plex_section.isdigit():
            raise ValueError("plex_section muss eine numerische ID sein")
        if item.plex_section and not item.plex_path:
            raise ValueError("plex_path fehlt")
        if item.plex_path and not PurePosixPath(item.plex_path).is_absolute() and not re.match(r"^[A-Za-z]:[\\/]", item.plex_path):
            raise ValueError("plex_path muss absolut sein")
        libs.append(item)
    if not libs or len({lib.name for lib in libs}) != len(libs):
        raise ValueError("Mindestens eine Mediathek mit eindeutigem Namen erforderlich")
    for first in libs:
        for second in libs:
            if first is not second and (contained(first.path, second.path) or contained(second.path, first.path)):
                raise ValueError("Überlappende Medienpfade sind nicht erlaubt")
    settings["state"] = Path(settings.get("state", "/config/state"))
    config = Config(libraries=tuple(libs), **settings)
    if not config.state.is_absolute() or any(contained(config.state, lib.path) for lib in libs):
        raise ValueError("state muss absolut und außerhalb der Medienpfade liegen")
    for key in ("dry_run", "replace_existing", "polling_enabled"):
        if type(getattr(config, key)) is not bool:
            raise ValueError(f"{key} muss ein Boolean sein")
    for lib in libs:
        if type(lib.season_numbering) is not bool:
            raise ValueError("season_numbering muss ein Boolean sein")
    for key in ("poll_seconds", "stable_seconds", "cache_seconds", "retry_seconds", "port"):
        if type(getattr(config, key)) is not int or getattr(config, key) < 1:
            raise ValueError(f"{key} muss eine positive Ganzzahl sein")
    if config.port > 65535:
        raise ValueError("Ungültiger Port")
    if config.plex_url:
        url = urlparse(config.plex_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError("Ungültige Plex-URL")
    return config