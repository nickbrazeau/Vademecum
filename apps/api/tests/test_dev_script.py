"""ADR 0004: `./scripts/dev.sh` has to run on the Bash 3.2 that macOS ships.

`wait -n` parses fine under 3.2 and only fails at runtime, after the API is up,
so a syntax check alone would not have caught it. These tests read the script
itself and exercise its supervision loop under the system Bash.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_SH = REPO_ROOT / "scripts" / "dev.sh"

BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")

# Constructs that a Bash 3.2 build rejects, with the version that introduced them.
BASH_4_ONLY = [
    (r"\bwait\s+-\w*n\b", "wait -n (4.3)"),
    (r"\b(mapfile|readarray|coproc)\b", "mapfile/readarray/coproc (4.0)"),
    (r"\b(declare|local|typeset)\s+-\w*A\b", "associative arrays (4.0)"),
    (r"\$\{[A-Za-z_]\w*(\[[^]]*\])?(\^|,)", "${var^^} / ${var,,} case conversion (4.0)"),
    (r"\bshopt\s+-s\s+(globstar|lastpipe|dirspell)\b", "globstar/lastpipe/dirspell (4.0)"),
    (r";;&", "case fallthrough ;;& (4.0)"),
    (r"&>>", "&>> append redirection (4.0)"),
    (r"\[\[[^]]*\s-v\s", "[[ -v var ]] (4.2)"),
    (r"\$\{[A-Za-z_]\w*(\[[^]]*\])?@[QEPAaKk]\}", "${var@Q} transformations (4.4)"),
    (r"\bexec\s+\{\w+\}", "{fd} automatic file descriptors (4.1)"),
]


def read_dev_sh() -> str:
    return DEV_SH.read_text(encoding="utf-8")


def supervise_definition() -> str:
    """The `supervise` function, lifted out of dev.sh so a test can run it."""
    match = re.search(r"^supervise\(\) \{$.*?^\}$", read_dev_sh(), re.S | re.M)
    assert match is not None, "dev.sh no longer defines supervise()"
    return match.group(0)


def run_bash(script: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_dev_sh_parses() -> None:
    result = subprocess.run(
        [BASH, "-n", str(DEV_SH)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_dev_sh_uses_no_construct_newer_than_bash_3_2() -> None:
    offenders = []
    for number, line in enumerate(read_dev_sh().splitlines(), start=1):
        code = line.split("#", 1)[0]
        for pattern, description in BASH_4_ONLY:
            if re.search(pattern, code):
                offenders.append(f"scripts/dev.sh:{number}: {description}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_supervise_returns_when_one_child_stops_and_leaves_the_other() -> None:
    """The trap cleans up the survivor, so supervise must not kill or wait on it."""
    started = time.monotonic()
    result = run_bash(
        supervise_definition()
        + """
sleep 30 & survivor=$!
sleep 1 & doomed=$!
supervise "$doomed" "$survivor"
kill -0 "$survivor" 2>/dev/null && echo SURVIVOR_STILL_RUNNING
kill "$survivor" 2>/dev/null
""",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "SURVIVOR_STILL_RUNNING" in result.stdout
    assert time.monotonic() - started < 15


def test_supervise_keeps_waiting_while_both_children_run() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_bash(
            supervise_definition()
            + """
sleep 5 & one=$!
sleep 5 & two=$!
supervise "$one" "$two"
echo RETURNED_TOO_EARLY
""",
            timeout=3,
        )


# --- the recorded pids must be the servers themselves -------------------------
#
# `(cd ... && cmd) &` records the *subshell's* pid in `$!`, not `cmd`'s. The
# supervise loop and the EXIT trap both work from those pids, so without `exec`
# the launcher watches and kills a wrapper while the server it started keeps
# running and keeps its port. `exec` replaces the subshell with the server, and
# the pid becomes the right one.

LAUNCH_LINES = {
    "api": r'^\(cd "\$API_DIR" && .*\) &$',
    "web": r'^\(cd "\$WEB_DIR" && .*\) &$',
}


def launch_line(which: str) -> str:
    match = re.search(LAUNCH_LINES[which], read_dev_sh(), re.M)
    assert match is not None, f"dev.sh no longer starts the {which} the way this test expects"
    return match.group(0)


def test_both_background_launches_exec_the_server() -> None:
    for which in LAUNCH_LINES:
        line = launch_line(which)
        assert re.search(r"&&\s+exec\s", line), (
            f"the {which} launch must exec so that $! is the server and not the "
            f"subshell that cleanup would kill instead: {line}"
        )


def test_web_launch_runs_vite_itself_not_npm() -> None:
    """`npm run dev` would put npm between the recorded pid and the server, and
    npm does not pass a terminating signal on to what it started."""
    line = launch_line("web")
    assert "node_modules/.bin/vite" in line, line
    assert "npm" not in line, line


def write_stub(path: Path, pid_file: Path, argv_file: Path) -> None:
    """A stand-in server: records its own pid and argv, then blocks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f'printf "%s" "$$" > "{pid_file}"\n'
        f'printf "%s\\n" "$@" > "{argv_file}"\n'
        # exec so a kill of the recorded pid really ends this stub, leaving no
        # stray sleep behind when the control run below kills the wrong pid.
        "exec sleep 20\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run_launch_harness(tmp_path: Path, *, with_exec: bool) -> dict[str, object]:
    """Run dev.sh's two launch lines verbatim against stub servers.

    Returns the pid bash recorded in `$!` and the pid each stub reported for
    itself. They match only when the subshell exec'd the stub.
    """
    api_dir = tmp_path / "api"
    web_dir = tmp_path / "web"
    venv = tmp_path / "venv"
    stubs = {
        "api": (venv / "bin" / "python", tmp_path / "api.pid", tmp_path / "api.argv"),
        "web": (
            web_dir / "node_modules" / ".bin" / "vite",
            tmp_path / "web.pid",
            tmp_path / "web.argv",
        ),
    }
    for path, pid_file, argv_file in stubs.values():
        write_stub(path, pid_file, argv_file)
    api_dir.mkdir(exist_ok=True)
    web_dir.mkdir(exist_ok=True)

    lines = {which: launch_line(which) for which in LAUNCH_LINES}
    if not with_exec:
        # The control: the same lines as they were before the fix.
        lines = {which: line.replace("&& exec ", "&& ") for which, line in lines.items()}

    script = f"""
set -uo pipefail
API_DIR={api_dir}
WEB_DIR={web_dir}
VENV={venv}
WEB_PORT=4321
{lines["api"]}
API_PID=$!
{lines["web"]}
WEB_PID=$!
for _ in $(seq 1 100); do
  if [ -s {stubs["api"][1]} ] && [ -s {stubs["web"][1]} ]; then break; fi
  sleep 0.1
done
echo "recorded_api=$API_PID"
echo "recorded_web=$WEB_PID"
kill "$API_PID" "$WEB_PID" 2>/dev/null
"""
    result = run_bash(script, timeout=30)
    recorded = dict(
        line.split("=", 1) for line in result.stdout.split() if line.startswith("recorded_")
    )
    reported = {
        which: int(pid_file.read_text()) if pid_file.exists() else None
        for which, (_, pid_file, _) in stubs.items()
    }
    # Whatever the launch did, leave nothing of it running.
    for pid in list(reported.values()) + [int(v) for v in recorded.values()]:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return {
        "recorded": {k.removeprefix("recorded_"): int(v) for k, v in recorded.items()},
        "reported": reported,
        "argv": {
            which: argv_file.read_text().split() if argv_file.exists() else []
            for which, (_, _, argv_file) in stubs.items()
        },
        "stderr": result.stderr,
    }


def test_launch_records_the_pid_of_the_server_itself(tmp_path: Path) -> None:
    run = run_launch_harness(tmp_path, with_exec=True)
    assert run["recorded"]["api"] == run["reported"]["api"], run["stderr"]
    assert run["recorded"]["web"] == run["reported"]["web"], run["stderr"]
    assert run["argv"]["api"] == ["-m", "vademecum"]
    assert run["argv"]["web"] == ["--port", "4321"]


def test_the_pid_check_would_fail_without_exec(tmp_path: Path) -> None:
    """Without `exec` the recorded pid is the subshell -- the bug being fixed.

    A control, so the test above cannot pass because bash happens to reuse the
    subshell for the command anyway.
    """
    run = run_launch_harness(tmp_path, with_exec=False)
    assert run["recorded"]["api"] != run["reported"]["api"]
    assert run["recorded"]["web"] != run["reported"]["web"]


# --- the real thing -----------------------------------------------------------
#
# The checks above run against stubs, so they stay fast and cannot be flaky. The
# test below starts the launcher for real, on ports nothing else uses and a data
# directory in tmp_path, and asserts the property the whole design rests on:
# when one server dies the launcher exits and takes the other one with it. Every
# wait is bounded, and the finally clause kills the process group whatever
# happens.

VENV_PYTHON = REPO_ROOT / "apps" / "api" / ".venv" / "bin" / "python"
VITE = REPO_ROOT / "apps" / "web" / "node_modules" / ".bin" / "vite"

READY_TIMEOUT = 90.0
EXIT_TIMEOUT = 30.0
PORT_RELEASE_TIMEOUT = 30.0

needs_a_real_checkout = pytest.mark.skipif(
    not (VENV_PYTHON.exists() and VITE.exists() and shutil.which("node") and shutil.which("lsof")),
    reason=(
        "needs an installed checkout (./scripts/dev.sh --check once), with node "
        "and lsof on PATH"
    ),
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def listening_pids(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [int(line) for line in result.stdout.split()]


def parent_pid(pid: int) -> int | None:
    result = subprocess.run(
        ["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=30
    )
    return int(result.stdout.strip()) if result.stdout.strip() else None


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_until(predicate, timeout: float, *, describe: str, log: Path) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError(
        f"{describe} did not happen within {timeout:.0f}s.\n"
        f"--- dev.sh output ---\n{log.read_text(errors='replace')}"
    )


@needs_a_real_checkout
def test_full_mode_stops_both_servers_when_one_of_them_dies(tmp_path: Path) -> None:
    api_port = free_port()
    web_port = free_port()
    log_path = tmp_path / "dev.log"

    environment = dict(os.environ)
    environment.update(
        VADEMECUM_PORT=str(api_port),
        VADEMECUM_WEB_PORT=str(web_port),
        VADEMECUM_DATA_DIR=str(tmp_path / "data"),
    )

    with log_path.open("w", encoding="utf-8") as log_file:
        launcher = subprocess.Popen(
            [BASH, str(DEV_SH)],
            cwd=str(REPO_ROOT),
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            # Its own process group, so the finally clause can clean up
            # everything it started without touching the test runner.
            start_new_session=True,
        )
    try:
        wait_until(
            lambda: answers(f"http://127.0.0.1:{api_port}/api/health"),
            READY_TIMEOUT,
            describe="the API became ready",
            log=log_path,
        )
        wait_until(
            lambda: answers(f"http://127.0.0.1:{web_port}/"),
            READY_TIMEOUT,
            describe="the web app became ready",
            log=log_path,
        )

        api_pids = listening_pids(api_port)
        web_pids = listening_pids(web_port)
        assert len(api_pids) == 1, f"expected one API listener, got {api_pids}"
        assert len(web_pids) == 1, f"expected one web listener, got {web_pids}"
        # The launcher's own children, not grandchildren behind a wrapper: this
        # is what makes the pids it recorded the pids it can actually kill.
        assert parent_pid(api_pids[0]) == launcher.pid
        assert parent_pid(web_pids[0]) == launcher.pid

        # Stop one of the two, the way a crash would.
        os.kill(api_pids[0], signal.SIGTERM)

        exit_code = launcher.wait(timeout=EXIT_TIMEOUT)
        assert exit_code != 0, "the launcher should exit non-zero when a server stops"

        wait_until(
            lambda: not listening_pids(api_port) and not listening_pids(web_port),
            PORT_RELEASE_TIMEOUT,
            describe=f"ports {api_port} and {web_port} were released",
            log=log_path,
        )
    finally:
        try:
            os.killpg(os.getpgid(launcher.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        launcher.wait(timeout=30)
        for port in (api_port, web_port):
            for pid in listening_pids(port):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
