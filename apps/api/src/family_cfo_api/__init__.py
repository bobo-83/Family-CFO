"""Family CFO API package."""

import pathlib

# Which BUILD file this package's version comes from (ADR 0074). The api and
# the worker ship as one image, so they share it.
_COMPONENT = "api"


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _read_repo_version() -> str:
    """This component's version (ADR 0074, amending ADR 0029): the repo-wide
    MAJOR.MINOR contract from /VERSION plus this component's own BUILD integer.

    The image bakes the composed string at /app/VERSION (docker/api.Dockerfile),
    so a container needs one read. In an editable checkout the two halves sit at
    the repo root and are composed here — reporting a bare contract would be a
    two-field version, and `_version_tuple` in backup_processing does not
    normalize length, so "0.157" would compare as older than "0.157.0".

    Falls back to a sentinel only if a half is genuinely absent.
    """
    baked = _read(pathlib.Path("/app/VERSION"))
    if baked:
        return baked

    repo_root = pathlib.Path(__file__).resolve().parents[4]
    contract = _read(repo_root / "VERSION")
    build = _read(repo_root / "apps" / _COMPONENT / "BUILD")
    if contract and build:
        return f"{contract}.{build}"
    return "0.0.0"


__version__ = _read_repo_version()
