# ADR 0019 — Claude as a model connection, through the Claude Code CLI

- Status: accepted
- Date: 2026-10-03
- Extends: [0006](0006-codex-app-server-bridge.md), [0018](0018-builds-on-a-timer.md)
- Keeps: [0002](0002-local-first-boundary.md) (no key, ever)

## Context

The owner uses both ChatGPT and Claude and wants the Mac's own model connection, the one that
builds on a timer and grades unattended, to be either. Until now that connection was the Codex
App Server bridge only. Claude Code, the CLI, is installed on the Mac and signed in with the
owner's own Claude account; it can run one non-interactive turn with a JSON schema and return
structured output.

## Decisions

### 1. A second connection with the same shape and the same rules

`VADEMECUM_MODEL_PROVIDER=claude` (`mcp.sh setup login --model claude`) makes the Mac's turns
run through `claude -p`: one call per turn, in the workspace's isolated empty directory, every
tool switched off, a single turn, the pipeline's output schema passed to the CLI, and the
output validated here again before anything is kept. The CLI's own text is never logged or
returned; a failure is a category, as at the Codex boundary. Signing in is the owner's act in
a terminal; this process never starts a login and never holds a key.

### 2. Everything that used the Codex connection works unchanged

Builds, evidence checks, question assessment, grading, and builds on a timer all go through
the one turn-runner interface. The Model page reads the CLI's own sign-in state. Disclosures
name the destination as Claude through the Claude Code CLI on this Mac.

### 3. Host mode is still the default for the assistant-driven product

Codex and Claude as assistants in a conversation remain the model in host mode (ADR 0009).
`codex` and `claude` modes are for the Mac's unattended work. The sea, Foris, stays in host
mode and never calls a model itself.

## Consequences

- Two providers, both on the owner's own subscription, neither through a key. The README's
  table of what is sent names both destinations.
- A Claude turn is a process start per turn, slower than the Codex bridge's long-lived child,
  which is fine for builds on a timer and grading.
- Claude Code's own usage limits apply; the schedule's batches per run and times a day remain
  the levers.
