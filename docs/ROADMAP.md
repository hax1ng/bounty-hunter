# Roadmap

## Done in this build (workspace)

- Target as the project unit (`target.json` plus `.bountyhunt.json` direct-open compatibility)
- File → New / Open / Save / Save As / Recent / Close / Quit
- Dirty indicator and unsaved prompt
- Burp / Binary Ninja-style chrome: tree, tabs, assistant, log, status bar
- Scope and per-role provider/model/reasoning editors (OpenAI + Anthropic)
- Dashboard/toolbar model picker with searchable model ids and one-role/all-role application
- Guided AI Target setup from pasted program material, with typed preview and separate operator authorization
- Async stub graph + EventBus + authorization/scope gates
- Assistant chat that does not hunt
- Two example SurfaceSlices, one stub Solver per slice, and strict finding gates (stub output never becomes a report)
- Oracle strategist loop: typed hypotheses, chains, and escalations; one concurrent Solver worker per slice; deterministic dedupe-key convergence; proposes-only under `ScopeGuard`
- Read-only Assistant backend/log context
- Evidence hard gate with typed reproduction steps and operator-supplied artifact paths
- pydantic-ai factory with `use_llm=False` by default
- Opt-in OpenAI Responses and Anthropic integration for every role with structured outputs
- Subscription-first routing per provider (Codex app-server / `claude --print`) with API-key fallback
- Codex structured-output recovery: wrapper extraction, one schema retry, safe validation diagnostics, then API fallback when available
- Ephemeral/environment API key handling for both providers; secrets are never persisted
- Continuous or timed hunt cycles with configurable delay, current-cycle status, Stop control, and cross-cycle artifact retention
- Continuous retry after transient provider/transport/structured-output failures

## Next

1. Replace local Mapping examples with authorized program adapters
2. Render confirmed chains/escalations in the Reporter at their escalated severity
3. Verification Solvers for high-priority chains
4. Add a configurable global Solver-concurrency limit and durable resume
5. Evidence file picker/capture UI for operator-provided screenshots and videos
6. Add usage/cost summaries per hunt
7. More platform report templates, **export only**
8. Optional native window polish (`--native`) and desktop file associations

## Not planned

- Auto-submit
- Unauthorized / out-of-scope helpers
- Multi-user hosted SaaS
