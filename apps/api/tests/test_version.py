"""The version scheme (ADR 0074): a MAJOR.MINOR contract plus a per-component
BUILD. `_read_repo_version` had no coverage at all before this."""

import pathlib

import family_cfo_api
from family_cfo_api import __version__, _read_repo_version
from family_cfo_api.backup_processing import _version_tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _reader(baked: str = "", contract: str = "", build: str = ""):
    """Stand in for the three files `_read_repo_version` consults."""

    def read(path: pathlib.Path) -> str:
        if str(path) == "/app/VERSION":
            return baked
        if path.name == "BUILD":
            return build
        if path.name == "VERSION":
            return contract
        return ""

    return read


def test_composes_contract_and_build(monkeypatch) -> None:
    monkeypatch.setattr(family_cfo_api, "_read", _reader(contract="0.157", build="4"))

    assert _read_repo_version() == "0.157.4"


def test_prefers_the_version_baked_into_the_image(monkeypatch) -> None:
    # docker/api.Dockerfile composes the full string at build time, so a
    # container must not re-derive it from files that are not there.
    monkeypatch.setattr(
        family_cfo_api, "_read", _reader(baked="0.157.9", contract="0.157", build="4")
    )

    assert _read_repo_version() == "0.157.9"


def test_falls_back_when_the_build_is_missing(monkeypatch) -> None:
    # Reporting a bare "0.157" would be a two-field version, and _version_tuple
    # does not normalize length — see test_bare_contract_sorts_below_any_build.
    monkeypatch.setattr(family_cfo_api, "_read", _reader(contract="0.157"))

    assert _read_repo_version() == "0.0.0"


def test_falls_back_when_the_contract_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(family_cfo_api, "_read", _reader(build="4"))

    assert _read_repo_version() == "0.0.0"


def test_reported_version_matches_the_repo_files() -> None:
    """The version the API reports is the one the repo actually declares."""
    contract = (REPO_ROOT / "VERSION").read_text().strip()
    build = (REPO_ROOT / "apps" / "api" / "BUILD").read_text().strip()

    assert __version__ == f"{contract}.{build}"


def test_a_contract_bump_outranks_any_build() -> None:
    """BUILD resets to 0 on a contract bump, so ordering has to survive the
    reset — otherwise the backup restore gate would refuse a newer box."""
    assert _version_tuple("0.156.9") < _version_tuple("0.157.0")


def test_builds_within_a_contract_order_numerically() -> None:
    assert _version_tuple("0.157.2") < _version_tuple("0.157.10")


def test_bare_contract_sorts_below_any_build() -> None:
    """A trap, pinned deliberately: `_version_tuple` does not pad, so a
    two-field string compares as OLDER than the same contract's build 0. This is
    why /VERSION is never reported directly and `scripts/check-versions.sh`
    refuses a three-field /VERSION."""
    assert _version_tuple("0.157") < _version_tuple("0.157.0")
