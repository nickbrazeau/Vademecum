"""Turning the App Server's tools off, in the two places they can be turned on.

A read-only sandbox is not a tool policy. It constrains what a tool may touch;
it does not stop the model reaching for one, and it does nothing at all about
MCP servers, which are separate processes with their own reach. The owner's
Codex install has features and MCP servers enabled by default, because the
desktop app wants them. Vademecum must not inherit that.

There are exactly two mechanisms, and both are needed:

**Startup flags** (`STARTUP_CONFIG_FLAGS`) are passed to the child as
``-c key=value`` by ``transport.SubprocessTransport``. They form a config layer
above the user's ``~/.codex/config.toml``, so they apply to every thread this
process will ever start, including one started by code that forgets to harden
itself.

**Per-thread config** (``thread_config``) exists because the startup layer
cannot express the one thing that matters most. Thread config is *deep-merged*
with the configured layers, so ``mcp_servers = {}`` clears nothing: an empty
table merged over two configured servers leaves two configured servers. Each
one has to be named and disabled. The names are read at runtime from
``config/read`` rather than written here, because a name hardcoded in this file
is a name that stops matching the day the owner adds a server.

The read happens **once per thread, not once per client**. The owner's
``~/.codex/config.toml`` is an ordinary file that an ordinary editor can change
while this process is running, and a name discovered an hour ago is a claim
about a file rather than about the config a thread is actually about to
inherit. Caching it made a new MCP server invisible until restart, which is the
one thing this module exists to prevent, so there is no cache.

If the effective configuration cannot be read, this module raises rather than
guessing. Starting a grading thread with unknown MCP state is the failure it
exists to prevent.

Verified by hand against ``codex-cli 0.153.4`` (see
``schemas/codex-app-server/MANIFEST.json``): the child starts with every flag
below, still answers ``initialize`` and ``account/read`` (with the owner's
``chatgpt`` account), and still accepts a ``thread/start`` carrying the config
this module builds. No flag was rejected -- including the five telemetry ones
and ``model_provider``, each of which was additionally confirmed to be a *real*
key by feeding it a wrong type and watching the CLI name the key in its parse
error. See ``OTHER_CONFIG`` for what each probe printed.
"""

from __future__ import annotations

import copy
from typing import Any

from . import protocol
from .diagnostics import event
from .errors import BridgeError, BridgeUnavailable

# Codex features that can call out, run something, see something, remember
# something, or install something. Each becomes `-c features.<name>=false`.
#
# The list is deliberately longer than the set of features that exist today:
# naming a feature the installed CLI does not have is harmless (the flag is an
# unknown config key under a table it already parses), while omitting one that
# does exist is a tool this bridge never meant to allow.
DISABLED_FEATURES: tuple[str, ...] = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "apps",
    "plugins",
    "hooks",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "multi_agent",
    "multi_agent_v2",
    "code_mode",
    "code_mode_host",
    "code_mode_prewarm",
    "view_image",
    "image_generation",
    "memories",
    "skill_search",
    "skill_mcp_dependency_install",
    "workspace_dependencies",
    "tool_suggest",
    "goals",
    "sleep_tool",
    "request_permissions_tool",
)

# Settings that are not feature flags.
#
# `web_search` is a string enum (WebSearchMode: disabled|cached|indexed|live),
# not a boolean. `-c web_search=false` makes the CLI exit 1 with "invalid type:
# unit variant, expected string" before it reads a line of stdin, which would
# present as `codex_not_found`-adjacent breakage rather than as a policy error.
# Verified by hand against 0.153.4.
#
# `notify` is cleared because a legacy notify hook is a command the child would
# run on the owner's behalf at the end of every turn.
# `project_doc_max_bytes=0` stops AGENTS.md-style project documents being read
# out of the turn's working directory and folded into the prompt.
# `skills.include_instructions=false` stops installed skill instructions being
# folded in the same way. Grading instructions come from the caller, and
# nothing else belongs in them.
#
# The five telemetry keys are the other half of AGENTS.md's logging rule. A
# grading prompt carries a learner's answer, and `otel.log_user_prompt` decides
# whether the child writes that answer into a span; the three exporters decide
# whether the span leaves the machine at all. `analytics.enabled=false` is the
# same argument for the CLI's own analytics. Each is off, by name, rather than
# left to a default that a Codex release may change.
#
# All five are typed keys in 0.153.4, not ignored strays: `otel.exporter=false`
# is rejected with "invalid type: unit variant, expected string only in
# `otel.exporter`", and `analytics.enabled="x"` with "expected a boolean in
# `analytics.enabled`". Verified by hand; not asserted in a test, because a test
# that asserted it would have to start a child.
#
# `model_provider="openai"` is the third layer of AGENTS.md boundary 4, under
# the `modelProvider` sent with every `thread/start` and the `modelProvider`
# checked on the reply. It pins the child to the built-in OpenAI provider, so a
# `~/.codex/config.toml` naming a custom OpenAI-compatible endpoint (which is an
# API key with extra steps) cannot be what a grading turn runs against, even on
# a code path that forgot to send the per-thread member.
#
# Verified by hand against 0.153.4, the same way as the telemetry flags, and it
# is a real key rather than an ignored stray in three ways:
#   * wrong type -- `-c model_provider=false` exits 1 with "invalid type:
#     boolean `false`, expected a string in `model_provider`";
#   * wrong value -- `-c model_provider="zzfakeprovider"` exits 1 with "Model
#     provider `zzfakeprovider` not found", so the value is resolved against the
#     provider registry rather than stored and forgotten;
#   * right value -- with `-c model_provider="openai"` the child starts, answers
#     `initialize`, and `config/read` reports `model_provider: "openai"` in the
#     merged configuration.
OTHER_CONFIG: tuple[tuple[str, str], ...] = (
    ("model_provider", '"openai"'),
    ("web_search", '"disabled"'),
    ("notify", "[]"),
    ("project_doc_max_bytes", "0"),
    ("skills.include_instructions", "false"),
    ("otel.log_user_prompt", "false"),
    ("otel.exporter", '"none"'),
    ("otel.trace_exporter", '"none"'),
    ("otel.metrics_exporter", '"none"'),
    ("analytics.enabled", "false"),
)


def _startup_flags() -> tuple[str, ...]:
    flags: list[str] = [f"features.{name}=false" for name in DISABLED_FEATURES]
    flags.extend(f"{key}={value}" for key, value in OTHER_CONFIG)
    return tuple(flags)


# `key=value` fragments, in the order they are passed. Frozen at import: this is
# a policy, not a setting, and nothing in the application may add to it.
STARTUP_CONFIG_FLAGS: tuple[str, ...] = _startup_flags()


def startup_argv() -> list[str]:
    """The `-c key=value` argument pairs, flattened for ``exec``."""
    argv: list[str] = []
    for flag in STARTUP_CONFIG_FLAGS:
        argv.extend(("-c", flag))
    return argv


# --- per-thread suppression --------------------------------------------------


async def discovered_mcp_servers(client: Any) -> tuple[str, ...]:
    """The effective MCP server names, read from the running child.

    Never a constant, and never cached. Every call is a fresh ``config/read``,
    because the answer is a property of a file the owner can edit between two
    threads and not a property of the process. Fails closed: an unreadable
    configuration means the MCP state is unknown, and an unknown MCP state is
    not something a grading thread may be started on top of.
    """
    try:
        response = await client.call(protocol.CONFIG_READ, protocol.config_read_params())
    except BridgeError as exc:
        event("appserver_config_read_failed", category=exc.category)
        raise BridgeUnavailable("config_unreadable") from exc
    except Exception as exc:
        # A transport or client failure that is not already a category. Still a
        # config we could not read, and still not something to guess about.
        event("appserver_config_read_failed", category="read_failed")
        raise BridgeUnavailable("config_unreadable") from exc

    names = _mcp_server_names(response)
    event("appserver_mcp_discovered", count=len(names))
    return names


def _mcp_server_names(response: Any) -> tuple[str, ...]:
    """Read `config.mcp_servers` out of a ConfigReadResponse.

    An *absent* `mcp_servers` is a failure rather than "none configured". The
    merged config always carries the table, so its absence means the key has
    moved -- and reading a moved key as an empty one would silently disable
    nothing while reporting success. An explicit ``null`` is the CLI saying the
    table is empty, and is read as such.
    """
    if not isinstance(response, dict):
        raise BridgeUnavailable("config_unreadable")
    config = response.get("config")
    if not isinstance(config, dict) or "mcp_servers" not in config:
        raise BridgeUnavailable("config_unreadable")
    servers = config["mcp_servers"]
    if servers is None:
        return ()
    if not isinstance(servers, dict):
        raise BridgeUnavailable("config_unreadable")
    return tuple(sorted(name for name in servers if isinstance(name, str)))


def build_thread_config(names: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Thread config that names every server it disables.

    Not ``mcp_servers = {}``: thread config is deep-merged, so an empty table
    merges to no change at all.
    """
    return {
        "mcp_servers": {name: {"enabled": False} for name in names},
        "tools": {"web_search": False},
    }


async def thread_config(client: Any) -> dict[str, Any]:
    """The `config` member of a hardened `thread/start`.

    Reads the effective config now, for this thread. See the module docstring
    for why that is not cached.
    """
    return build_thread_config(await discovered_mcp_servers(client))


# A defensive copy, so a caller that mutates what it was handed cannot edit a
# policy the next caller relies on.
def copy_thread_config(config: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(config)
