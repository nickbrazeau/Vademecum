"""ADR 0002 and ADR 0003: the boundaries that are refusals, not warnings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vademecum.config import (
    BIND_HOST,
    CHATGPT_APP_CODEX,
    CODEX_CANDIDATES,
    DATA_SUBDIRECTORIES,
    ConfigError,
    Settings,
    default_data_dir,
    find_repo_root,
    is_loopback,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.0.0.53"])
def test_loopback_addresses_are_recognised(host: str) -> None:
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com", ""])
def test_non_loopback_addresses_are_recognised(host: str) -> None:
    assert not is_loopback(host)


def test_default_bind_is_loopback() -> None:
    assert Settings().host == "127.0.0.1"


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "192.168.1.10", "::", "example.com", "", "0.0.0.0 "]
)
def test_a_non_loopback_bind_is_refused(host: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings(host=host).check_bind()
    assert "127.0.0.1" in str(excinfo.value)


@pytest.mark.parametrize("host", ["::1", "localhost", "127.0.0.53"])
def test_even_another_loopback_address_is_refused(host: str) -> None:
    """AGENTS.md names one address. Anything else is a different promise."""
    with pytest.raises(ConfigError):
        Settings(host=host).check_bind()


def test_there_is_no_setting_that_turns_the_bind_rule_off() -> None:
    """A boundary with an off switch is not a boundary."""
    assert "allow_non_loopback" not in Settings.model_fields
    with pytest.raises(ConfigError):
        Settings(host="0.0.0.0", allow_non_loopback=True).check_bind()


def test_the_removed_opt_in_environment_variable_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VADEMECUM_HOST", "0.0.0.0")
    monkeypatch.setenv("VADEMECUM_ALLOW_NON_LOOPBACK", "true")
    with pytest.raises(ConfigError):
        Settings().check_bind()


def test_the_only_accepted_bind_is_the_documented_one() -> None:
    Settings(host=BIND_HOST).check_bind()
    assert BIND_HOST == "127.0.0.1"


def test_data_dir_inside_the_source_tree_is_refused() -> None:
    repo_root = find_repo_root()
    assert repo_root is not None, "tests must run from inside the checkout"
    with pytest.raises(ConfigError) as excinfo:
        Settings(data_dir=repo_root / "apps" / "api" / "data").resolve_data_dir()
    assert "VADEMECUM_DATA_DIR" in str(excinfo.value)

    with pytest.raises(ConfigError):
        Settings(data_dir=repo_root).resolve_data_dir()


def test_prepare_creates_every_subdirectory(tmp_path: Path) -> None:
    root = Settings(data_dir=tmp_path / "vm").prepare()
    assert root.is_dir()
    for name in DATA_SUBDIRECTORIES:
        assert (root / name).is_dir(), f"{name} should exist at boot, not on first use"


def test_default_data_dir_is_outside_any_checkout() -> None:
    assert Path.home() in default_data_dir().parents


# --- the Codex CLI the bridge manages (ADR 0006) -----------------------------


def test_the_codex_path_is_configurable_and_absolute(tmp_path: Path) -> None:
    settings = Settings(codex_path=tmp_path / ".." / "codex")
    resolved = settings.resolve_codex_path()
    assert resolved.is_absolute()
    assert ".." not in resolved.parts


def test_an_unset_codex_path_falls_back_to_a_named_candidate() -> None:
    """A path that may not exist, on purpose.

    An absent Codex is an interface state ("not installed"), not a refusal to
    start: the bridge is optional until the owner opens the Model page.
    """
    resolved = Settings().resolve_codex_path()
    assert resolved.is_absolute()
    assert resolved in {candidate.resolve() for candidate in CODEX_CANDIDATES} or resolved.exists()


def test_the_chatgpt_app_is_the_first_place_looked(tmp_path: Path) -> None:
    assert CODEX_CANDIDATES[0] == CHATGPT_APP_CODEX
    assert CHATGPT_APP_CODEX.name == "codex"


def test_the_bridge_timeouts_are_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(appserver_request_timeout=0)
    with pytest.raises(ValidationError):
        Settings(appserver_startup_timeout=10_000)
    assert Settings().appserver_request_timeout == 30.0
    assert Settings().appserver_startup_timeout == 20.0


def test_the_api_stops_when_the_process_that_started_it_is_gone(monkeypatch, tmp_path) -> None:
    """ADR 0012: an orphaned API must not keep an old build on the port."""
    import asyncio
    import signal

    from vademecum.app import _watch_parent
    from vademecum.config import Settings

    assert Settings(data_dir=tmp_path / "d").parent_pid is None
    monkeypatch.setenv("VADEMECUM_PARENT_PID", "4242")
    assert Settings(data_dir=tmp_path / "d").parent_pid == 4242

    raised: list[int] = []
    parents = iter([4242, 4242, 1])
    monkeypatch.setattr("os.getppid", lambda: next(parents))
    monkeypatch.setattr(signal, "raise_signal", lambda signum: raised.append(signum))
    asyncio.run(_watch_parent(4242, interval=0.001))
    assert raised == [signal.SIGTERM]
