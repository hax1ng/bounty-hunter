# Agents

Roles are fixed. Provider (OpenAI or Anthropic), model, and reasoning are per-role on the Target. The searchable editor is available from **Dashboard → Choose AI models**, the toolbar model chip, and the original Scope view. Every role has a local-stub default. When its policy explicitly sets `use_llm=True`, the live adapter (`agents/live.py`) tries the provider's subscription first — a ChatGPT-authenticated Codex app-server turn for OpenAI, or a `claude --print` turn on the Claude Pro/Max login for Anthropic. If unavailable, `agents/factory.py` builds an OpenAI Responses or Anthropic pydantic-ai Agent using that provider's optional API key. Every route uses typed outputs and exposes no hunt tools.

Reasoning levels (`none · low · medium · high · xhigh · max`) are provider-neutral: they map to OpenAI reasoning effort, and to Claude's `--effort` / extended thinking (`none` disables thinking). See `agents/reasoning.py`.

| Role | Hunts? | Job |
| --- | --- | --- |
| **Assistant** | no | Answers the user and reads a bounded backend snapshot plus recent logs. Never maps, exploits, or writes reports. |
| **Organizer** | no (orchestrates) | Continuously owns the hunt graph, task queue, pause/stop/timer controls, and asyncio fan-out. Pure mechanism. |
| **Oracle** | no (proposes) | Strategist. Drives systematic hypothesis, chain, and impact-escalation loops. The Organizer schedules its typed directives under the same gates. |
| **Mapping** | yes | Turns the declared in-scope / out-of-scope attack surface into bounded **SurfaceSlices**. |
| **Solver** | yes | **One agent per slice.** Webhook secrets ≠ invoice export. |
| **Dedup** | yes | Duplicate risk against other local candidate titles; it has no public-report lookup. |
| **Devil’s advocate** | yes | Kill informative, spam, and weak reports before they waste a slot. |
| **Evidence** | yes | Extracts operator-verified reproduction steps and screenshot/video/request-response paths; missing evidence blocks reporting. |
| **Reporter** | no (writes only) | Platform-specific write-up (HackerOne, Bugcrowd, …). **Never auto-submits.** |

## Why one Solver per slice

A single “hunt the whole program” agent smears context. Payment-export IDOR and a Slack webhook leak are different bugs, different impact, different evidence. Mapping must cut the surface so each Solver stays inside one slice. The same invariant applies inside the Oracle loop: hypotheses for different slices run concurrently, while multiple hypotheses for one slice are batched into one Solver turn.

## The Oracle loop

The Oracle is the strategist, and it never touches the target. It reads the surface map and the findings so far and emits **typed directives** — falsifiable `Hypothesis` records, `Chain` proposals across two or more findings, and `Escalation` proposals on a single finding.

The Organizer runs it as a bounded loop after the baseline Solver sweep:

1. `oracle_plan` (live) or a converged no-op (stub) returns proposals.
2. The Organizer derives a deterministic `dedupe_key` for each and absorbs only the fresh ones — skipping keys already seen this hunt — and persists them on the Target (`hypotheses`, `chains`, `escalations`).
3. Fresh hypotheses are grouped by slice. The Organizer starts **one focused Solver turn per slice**, assessing that slice's hypotheses as one typed batch while other slices run in parallel. A declared-but-unmapped in-scope area receives a freshly minted bounded slice. `ScopeGuard.require_slice` fires before every Solver, so the propose/schedule split never widens scope.
4. A confirmed hypothesis becomes a Finding tagged with its `hypothesis_id`.
5. The loop stops when the Oracle reports `converged`, after two dry rounds with nothing new, or at a fixed round cap.

Testing a hypothesis needs a **live Solver**; with a stub Solver, fresh hypotheses are marked `deferred` for the operator rather than fabricated. A **stub Oracle is a no-op** — one converged round. Local example candidates are killed by the advocate gate and never become reports. Chains and escalations are recorded and shown in the Oracle tab, but they remain proposals until their demonstrations are operator-verified; unverified strategy never changes report severity.

## Assistant vs Organizer

The GUI chat is the Assistant. It receives read-only hunt state, slices, findings, directives, report metadata, and the 20 most recent events so it can answer backend/log questions. Starting or mutating a hunt is the Organizer. If the Assistant ever starts calling tools that hit the target, that is a bug.

## Guided setup

Guided setup uses the selected Assistant policy for one structured extraction
turn over text pasted by the operator. It returns `TargetSetupPlan`: name,
platform, program URL, explicit in/out-of-scope assets, and notes. It does not
browse, hunt, or set authorization. The operator reviews the draft and checks
the authorization attestation separately. By default, applying the draft also
assigns the selected provider/model/reasoning to every enabled role and opts
those roles into hosted AI; the dialog provides a checkbox to disable that
convenience.

## Continuous cycles and model failures

Each graph pass remains bounded, including the Oracle's round limit. With
`continuous_hunt=True`, the Organizer waits `hunt_cycle_delay_seconds` and
starts another complete pass until Stop or `hunt_time_limit_minutes`. Prior
findings, directives, and reports are merged by stable id/dedupe key. The status
bar exposes the current cycle.

Codex structured-output parsing accepts a bare object, a fenced object, or an
otherwise valid object with a short accidental wrapper. Invalid output is
retried once with the same schema in the same ephemeral thread. If the
subscription retry fails, the configured API key remains the immediate
fallback. Without an API fallback, continuous mode logs the model failure and
retries on the next cycle rather than ending the entire hunt; one-pass mode
stops with a safe field-level diagnostic.

## Defaults

| Role | Model | Reasoning |
| --- | --- | --- |
| Assistant | gpt-5.6-luna | low (stay snappy) |
| Organizer | gpt-5.6-terra | medium |
| Oracle | gpt-5.6-sol | xhigh |
| Mapping | gpt-5.6-terra | medium |
| Solver | gpt-5.6-sol | high |
| Dedup | gpt-5.6-luna | low |
| Devil | gpt-5.6-terra | high |
| Evidence | gpt-5.6-terra | medium |
| Reporter | gpt-5.6-terra | medium |

Defaults use the OpenAI provider. Switch any role to Anthropic (e.g. `claude-sonnet-4-6` or `claude-opus-4-8`) from the Dashboard model dialog or Scope policy panel. Override per Target. Disabled roles are skipped. Live output never changes the deterministic safety gates or submission boundary.
