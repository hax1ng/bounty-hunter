# Architecture

Bounty Hunter is a single-user Linux workbench. The GUI is a NiceGUI app launched by `bountyhunter`. There is no multi-tenant server and the UI binds to localhost by default.

## Project unit: Target

A **Target** is the hunt/project. It is the File → New / Open / Save unit.

On disk it is a folder:

```
<target>/
  target.json          # canonical pydantic Target manifest (schema_version 1)
  .bountyhunt.json     # compatibility/direct-open manifest
  events.jsonl         # append-only typed HuntEvents
  scope.md             # human-readable dump of in/out of scope
  notes.md
  slices/              # serialized SurfaceSlices
  findings/
  evidence/            # screenshot / video paths
  reports/             # exported write-ups; never auto-submitted
```

Open either the folder or `.bountyhunt.json` (alias: `bountyhunt.json`).

App settings (recent targets) live in the platform config dir, e.g. `~/.config/bountyhunter/settings.json`.

## Process

```
CLI (typer)
  └─ NiceGUI page  (Assistant stays here)
        ├─ Session (in-memory Target + dirty fingerprint)
        ├─ HuntStore (folder, artifacts, events)
        └─ Organizer / EventBus  (one pass or repeated cycles)
              ├─ Oracle    ─┐  strategist: hypotheses / chains / escalations (proposes only)
              ├─ Mapping    │
              ├─ Solver × N ├─ asyncio fan-out (one concurrent worker per slice)
              ├─ Dedup      │
              ├─ Devil      │
              ├─ Evidence   │
              └─ Reporter  ─┘  never submits
```

The Assistant is the only agent that talks to the user. It receives a read-only backend snapshot (including recent logs) through `HuntDeps`, but does not call Mapping or Solver or mutate state. The Organizer owns the graph and schedules `run_hunt` using `asyncio.create_task`, so the GUI/chat stays responsive. `EventBus` fans typed `HuntEvent` records into the bottom logger and `HuntStore` appends them to `events.jsonl`.

The guided Target setup action reuses the selected Assistant policy for one
tool-free structured extraction turn. `TargetSetupPlan` can contain a name,
platform, program URL, explicit scope lists, and notes, but deliberately has no
authorization field. The UI shows the draft before applying it and requires the
operator's separate authorization attestation.
Applying a guided draft assigns the selected provider/model/reasoning to every
enabled role by default; the operator can disable that option before applying.

After the baseline Solver sweep the Organizer runs the Oracle as a bounded loop: the Oracle proposes typed directives (hypotheses, chains, escalations), the Organizer derives a deterministic `dedupe_key`, absorbs only the fresh ones, groups hypotheses by slice, and repeats until the Oracle converges or a dry-round/round cap is hit. One typed Solver turn assesses each slice's batch; different slices fan out concurrently. The Oracle proposes only; scheduling and the scope gate stay with the Organizer. `organizer/oracle.py` holds the pure keying/absorption logic (unit-tested independently of the loop).

The post-strategy path is a strict promotion pipeline: **Dedup → Devil's advocate → Evidence → Reporter**. Evidence must contain operator-supplied reproduction steps before a candidate remains `reportable`; otherwise it becomes `needs_evidence`. Screenshot/video/request-response paths are accepted only when they already occur in operator material. The Reporter can improve summary and impact prose, but its reproduction section is rendered deterministically from the verified Evidence package.

`run_hunt` wraps that pipeline in either one-pass or continuous operation.
Continuous mode waits the configured interval and starts another complete pass
until Stop, an optional monotonic deadline, a scope violation, or a non-transient
error. Provider/transport/structured-output failures are logged and retried on
the next cycle instead of permanently stopping the hunt.
Findings, directives, and report previews from completed cycles are merged by
their stable ids/dedupe keys, so later work does not discard earlier artifacts.
The persisted controls are `continuous_hunt`, `hunt_time_limit_minutes` (`0`
means no timer), and `hunt_cycle_delay_seconds`. `GuiState.hunt_cycle` is
ephemeral and drives the current-cycle status display.

Live roles are subscription-first per provider; `agents/live.py` routes each role by its configured `ModelProvider`.

- **OpenAI:** `agents/codex_subscription.py` uses the local Codex app server and its existing ChatGPT login for one ephemeral structured-output turn. It registers no dynamic tools, disables built-in execution/network features, and uses an empty read-only/no-network sandbox.
- **Anthropic:** `agents/claude_subscription.py` shells out to `claude --print` on the existing Claude Pro/Max login for one ephemeral turn with `--tools ""` (all tools disabled), `--strict-mcp-config` (no MCP), no session persistence, `--json-schema` structured output, and an empty temporary working directory.

If a subscription route is unavailable and that provider's API key is configured, `agents/factory.py` falls back to pydantic-ai's OpenAI Responses model or Anthropic model — structured outputs, no tools, explicit usage limits. Reasoning level → provider knob mapping (effort / extended thinking) and per-level token budgets live in `agents/reasoning.py`.

The Codex path validates the final agent message against the role's pydantic
schema. It tolerates accidental Markdown/prose wrapping around an otherwise
valid JSON object, then retries one invalid response in the same ephemeral
thread with the identical schema. Validation errors include field paths and
messages but exclude returned field values. After that retry, the normal API
fallback is used when configured. A remaining `RuntimeError` stops a one-pass
hunt but is treated as a retryable cycle failure in continuous mode.

## Dirty tracking

`Target.fingerprint()` is canonical JSON excluding `updated_at`. The Session keeps a snapshot from the last save (or empty for unsaved). Title bar shows `Bounty Hunter — Name*`.

## Safety in the data path

- `create_target` refuses `authorized=False`.
- Organizer.start() calls `ScopeGuard.require_huntable` (authorization and non-empty scope).
- `ScopeGuard.require_huntable` rejects empty scope and exact in-scope/out-of-scope conflicts.
- `ScopeGuard.require_slice` runs immediately before every Solver queue operation.
- Continuous cycles keep the original in/out-of-scope snapshot immutable and run the same gates on every pass.
- File-backed slice/finding ids are validated before they can become artifact paths.
- A full hunt and manual Solver runs cannot overlap; duplicate manual runs on one slice are rejected.
- Reporter stub cannot submit; there is no submit API.
- OpenAI and Anthropic keys are ephemeral/environment-only and excluded from Target persistence.
- The provider CLIs own subscription credentials; Bounty Hunter checks `codex login status` / `claude auth status` but never reads or persists their cached tokens.
- The subscription-first preferences (per provider) are app-level UI configuration, not Target data.

## GUI layout

Dense dark chrome, not a marketing page.

1. Header: File / Target / Hunt / View / Help
2. Toolbar: New, Open, Save, Start / Pause / Stop, searchable model-policy chip
3. Body splitters: target tree | tabs | assistant + roster / event log
4. Footer: hunt id, phase/current cycle, live Solvers, scope, authorization, dirty state

## What this build does vs later

**Now:** Target persistence, guided AI setup, dashboard/searchable model-policy editing, local stubs plus opt-in OpenAI/Anthropic roles, continuous or timed async hunt cycles with the Oracle strategist loop, one concurrent Solver worker per slice, live event stream, strict findings/evidence gates, and export-only report preview.

**Later:** real authorized mapping adapters, richer evidence capture, Reporter rendering of confirmed chains/escalations at their escalated severity, verification Solvers for high-priority chains, usage/cost dashboards, and more platform templates (still no auto-submit).
