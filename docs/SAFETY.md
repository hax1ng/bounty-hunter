# Safety

Bounty Hunter is for **authorized** testing only: active bug bounty programs and written permission.

## Hard rules

1. **Authorization checkbox is required** to create a Target. There is no “I’ll do it later” path that still writes a hunt folder.
2. **Organizer will not start** if `target.authorized` is false (including a hand-edited manifest).
3. **Organizer will not start with empty or exactly conflicting scope.** The same asset cannot appear in both lists. `ScopeGuard` also checks every slice immediately before its Solver is queued.
4. **Stay in scope.** Out-of-scope assets are stored on the Target and shown in the tree. Mapping and Solver treat them as forbidden. The **Oracle only proposes** — it never tests assets. The Organizer schedules its hypotheses under the same `ScopeGuard`, and any slice minted for a declared-but-unmapped in-scope asset is re-gated before its Solver runs.
5. **Reporter never auto-submits.** Export a write-up, submit it yourself on HackerOne / Bugcrowd / Intigriti / Immunefi.
6. **GUI binds to 127.0.0.1 by default.** Do not expose the workspace on a LAN.
7. **No drive-by scanning.** This build uses stub/local example data and does not scan the public internet.
8. **Hosted models are opt-in per role.** Each role chooses OpenAI or Anthropic. Enabling it sends only that role's typed Target context to the chosen provider. Subscription credentials remain owned by the provider CLI (Codex / Claude Code); API keys remain process-memory/environment secrets. Neither is persisted by `HuntStore`.
9. **Unverified output cannot become a report.** Stub candidates are always killed. A live candidate must pass the advocate gate and contain operator-supplied reproduction steps extracted by Evidence; otherwise it is marked `needs_evidence`.
10. **Guided setup cannot authorize a Target.** The setup model treats pasted material as untrusted reference text, only extracts a typed draft, has no browse/hunt tools, and its output schema contains no authorization field. The operator must review the scope and separately attest authorization.
11. **Continuous still means bounded.** Every new cycle reuses the immutable scope snapshot and runs every existing ScopeGuard/report-quality gate. Stop cancels background work immediately; the optional timer uses the same stop path.
12. **Model retries do not relax validation.** Codex gets one retry with the identical structured-output schema and the same tool-free boundary. Continuous mode may retry a failed model cycle, but it does not bypass scope, evidence, authorization, or report-quality gates.
13. **One concurrent Solver per slice.** Distinct slices fan out, while same-slice hypotheses are batched into one Solver turn. Manual Solver runs cannot overlap a full hunt or another run on the same slice.

## What we will not add

- A “quick scan this IP” button with no Target / no attestation
- Auto-submit to any platform
- Helpers for attacking assets that are out of scope
- Binding to `0.0.0.0` as the default

## Operator duty

The checkbox is an attestation, not a lawyer. You still have to read the program policy. If you are not sure an asset is in scope, it is out of scope.
