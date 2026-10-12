"""`setup cloud` (ADR 0036): a learner's own cloud copy in one command. Wrangler is played
by a fake that records every call; the Keychain by a dictionary. What is pinned: the order
of the steps, that no secret is ever on a command line or printed, that a failed step stops
everything and says which, and that running again does not make a second key."""

from __future__ import annotations

from pathlib import Path

import pytest

from vademecum_mcp import cloud_setup
from vademecum_mcp.cloud_setup import Result, SetupFailed

ADDRESS = "https://vademecum-seat.learner.workers.dev"


class FakeKeychain:
    SYNC_TOKEN = "Vademecum sync token"
    BACKUP_KEY = "Vademecum cloud backup key"

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get(self, service: str, account: str) -> str | None:
        return self.items.get((service, account))

    def put(self, service: str, account: str, secret: str) -> None:
        self.items[(service, account)] = secret


class FakeWrangler:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.fail_at = fail_at

    def __call__(self, args: list[str], *, secret: str | None = None) -> Result:
        self.calls.append((args, secret))
        if self.fail_at and self.fail_at in " ".join(args):
            return Result(1, "error: something went wrong")
        if args[0] == "deploy":
            return Result(0, f"Uploaded vademecum-seat\nDeployed vademecum-seat triggers\n  {ADDRESS}\nCurrent Version ID: abc")
        return Result(0, "ok")


@pytest.fixture
def worker(tmp_path: Path) -> Path:
    folder = tmp_path / "worker"
    folder.mkdir()
    template = Path(__file__).resolve().parents[3] / "deploy" / "cloudflare" / "worker" / "wrangler.jsonc"
    (folder / "wrangler.jsonc").write_text(template.read_text())
    return folder


@pytest.fixture(autouse=True)
def settings_file(tmp_path: Path, monkeypatch):
    from vademecum.config import get_settings

    target = tmp_path / "settings.env"
    monkeypatch.setenv("VADEMECUM_SETTINGS_FILE", str(target))
    monkeypatch.delenv("VADEMECUM_SYNC_TOKEN", raising=False)
    monkeypatch.delenv("VADEMECUM_SYNC_PEER_URL", raising=False)
    get_settings.cache_clear()
    yield target
    get_settings.cache_clear()


def test_a_new_copy_in_order_with_every_secret_on_standard_input(worker: Path, capsys, settings_file: Path) -> None:
    run, keychain = FakeWrangler(), FakeKeychain()
    outcome = cloud_setup.create(run=run, keychain=keychain, worker_dir=worker, passphrase="a long recovery phrase", say=print)
    commands = [" ".join(args) for args, _secret in run.calls]
    assert commands[0] == "whoami"
    assert commands[1].startswith("r2 bucket create vademecum-")
    assert commands[2] == "deploy -c wrangler.local.jsonc"
    assert [c.split()[2] for c in commands[3:7]] == ["SEAT_KEY", "VADEMECUM_SYNC_ACCEPT_TOKEN", "VADEMECUM_MCP_PASSPHRASE", "SEAT_BACKUP_KEY"]
    assert commands[7] == "deploy -c wrangler.local.jsonc", "the second deploy tells the copy its address"
    assert outcome["address"] == ADDRESS

    secrets_given = [secret for _args, secret in run.calls if secret]
    assert len(secrets_given) == 4 and "a long recovery phrase" in secrets_given
    printed = capsys.readouterr().out
    for secret in secrets_given:
        assert all(secret not in " ".join(args) for args, _s in run.calls), "never on a command line"
        assert secret not in printed, "never printed"
    local = (worker / cloud_setup.LOCAL_CONFIG).read_text()
    assert f'"PUBLIC_URL": "{ADDRESS}"' in local and outcome["bucket"] in local
    host = "vademecum-seat.learner.workers.dev"
    assert keychain.get(keychain.BACKUP_KEY, host) in secrets_given
    assert keychain.get(keychain.SYNC_TOKEN, host) in secrets_given
    assert ADDRESS in settings_file.read_text() and keychain.get(keychain.SYNC_TOKEN, host) not in settings_file.read_text()


def test_a_failed_step_stops_everything_and_says_which(worker: Path) -> None:
    run = FakeWrangler(fail_at="r2 bucket create")
    with pytest.raises(SetupFailed, match="making the backup bucket"):
        cloud_setup.create(run=run, keychain=FakeKeychain(), worker_dir=worker, passphrase="a long recovery phrase", say=print)
    assert [args[0] for args, _ in run.calls] == ["whoami", "r2"], "nothing after the failure"


def test_a_short_passphrase_is_refused_before_anything_is_made(worker: Path) -> None:
    run = FakeWrangler()
    with pytest.raises(SetupFailed):
        cloud_setup.create(run=run, keychain=FakeKeychain(), worker_dir=worker, passphrase="short", say=print)
    assert run.calls == []


def test_adopting_seals_an_existing_copy_and_moves_the_token(capsys, settings_file: Path) -> None:
    from vademecum.config import get_settings

    settings_file.write_text(f'VADEMECUM_SYNC_PEER_URL="{ADDRESS}"\nVADEMECUM_SYNC_TOKEN="the-old-token-in-the-file"\n')
    get_settings.cache_clear()
    run, keychain = FakeWrangler(), FakeKeychain()
    first = cloud_setup.adopt(run=run, keychain=keychain, address=ADDRESS, say=print)
    assert [" ".join(args) for args, _ in run.calls] == ["whoami", "secret put SEAT_BACKUP_KEY", "deploy"]
    host = "vademecum-seat.learner.workers.dev"
    key = keychain.get(keychain.BACKUP_KEY, host)
    assert run.calls[1][1] == key and key not in capsys.readouterr().out
    assert keychain.get(keychain.SYNC_TOKEN, host) == "the-old-token-in-the-file"
    assert "the-old-token" not in settings_file.read_text()
    assert first["made_key"] is True

    get_settings.cache_clear()
    again = cloud_setup.adopt(run=FakeWrangler(), keychain=keychain, address=ADDRESS, say=print)
    assert again["made_key"] is False and keychain.get(keychain.BACKUP_KEY, host) == key, "running again keeps the key"
    assert again["fingerprint"] == first["fingerprint"]
