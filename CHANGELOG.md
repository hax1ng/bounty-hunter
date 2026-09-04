# Changelog

## 0.7.0 — 2026-09-03

### Added

- Dashboard and toolbar access to a searchable provider/model/reasoning editor.
- One-role and all-role model policy application.
- Guided AI Target setup from pasted program material, with a typed review step
  and separate operator authorization attestation.
- Optional assignment of the guided-setup model to every enabled role.
- Continuous hunt cycles, an optional timer, a configurable cycle delay, a
  visible cycle counter, and an explicit Stop control.
- Cross-cycle retention of findings, Oracle directives, and report previews.

### Improved

- Continuous Start now redirects to model setup when every hunt role is still
  a local stub, preventing repeated demonstration-only cycles.
- Transient model/transport failures no longer end continuous mode; preserved
  artifacts remain available and a later cycle retries.
- Codex subscription structured output now tolerates accidental wrappers,
  retries schema validation once in the same ephemeral tool-free thread, and
  reports safe field-level diagnostics before using an available API fallback.
- Model and run controls are easier to discover from the Dashboard.

### Safety

- Guided setup cannot authorize a Target and cannot browse or test assets.
- Every continuous cycle uses the original immutable scope snapshot and the
  existing authorization, scope, evidence, and report-quality gates.
- Reporter behavior remains export-only and never auto-submits.

