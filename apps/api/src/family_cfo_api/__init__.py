"""Family CFO API package."""

import pathlib


def _read_repo_version() -> str:
    """The monorepo's single version (M120, ADR 0029). Baked into the image at
    /app/VERSION; in an editable checkout it sits at the repo root. Falls back to
    a sentinel only if the file is genuinely absent."""
    for candidate in (
        pathlib.Path("/app/VERSION"),
        pathlib.Path(__file__).resolve().parents[4] / "VERSION",
    ):
        try:
            text = candidate.read_text().strip()
            if text:
                return text
        except OSError:
            continue
    return "0.0.0"


__version__ = _read_repo_version()
