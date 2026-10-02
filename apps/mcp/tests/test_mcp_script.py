"""`scripts/mcp.sh` has to run on the Bash 3.2 that macOS ships, like dev.sh."""

from __future__ import annotations

import re
import subprocess

from tests.mcp_support import REPO_ROOT  # noqa: I001  (puts the API's tests on sys.path)
from test_dev_script import BASH, BASH_4_ONLY  # noqa: E402

MCP_SH = REPO_ROOT / "scripts" / "mcp.sh"


def test_mcp_sh_parses() -> None:
    result = subprocess.run([BASH, "-n", str(MCP_SH)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_mcp_sh_uses_no_construct_newer_than_bash_3_2() -> None:
    offenders = []
    for number, line in enumerate(MCP_SH.read_text(encoding="utf-8").splitlines(), start=1):
        code = line.split("#", 1)[0]
        for pattern, description in BASH_4_ONLY:
            if re.search(pattern, code):
                offenders.append(f"scripts/mcp.sh:{number}: {description}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_mcp_sh_is_executable_and_refuses_unknown_arguments() -> None:
    assert MCP_SH.stat().st_mode & 0o111
    result = subprocess.run([BASH, str(MCP_SH), "--nonsense"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 64
    assert "usage" in result.stderr


def test_claude_code_config_points_at_stdio_mode() -> None:
    import json

    config = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["vademecum"]
    assert server["command"].endswith("scripts/mcp.sh")
    assert server["args"] == ["--stdio"]
