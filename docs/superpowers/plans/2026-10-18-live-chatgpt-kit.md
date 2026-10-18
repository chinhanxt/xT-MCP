<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Live ChatGPT kit implementation plan

**Goal:** Prepare a reusable Vietnamese operator kit; perform only offline checks.

**Architecture:** Keep host/UI observations distinct from independently queried DB
effects. Reuse psycopg and existing tenant, workflow, operation and audit schemas.
Use one repeatable-read, read-only transaction with unconditional rollback and
explicit tenant predicates. Never invoke the application context setter, which writes.

**Spec:** User request; `docs/mcp-chatgpt-20261017.md` and
`docs/plans/2026-10-17-unified-xt-studio-waretrack.md` §9.

## Constraints

- No browser startup, login, ChatGPT calls, public-stack changes or Docker tests.
- Do not edit deploy configuration or Desk chat/LLM state.
- Credentials/cookies and raw evidence remain outside the repository.
- New files carry SPDX identifiers; commit includes the requested co-author trailer.
- Push `live-chatgpt-kit`; open a draft PR to `staging`; do not merge.

## Work and validation

1. Read local guidance, historical observations, browser helpers and current contracts.
   Separate documentation, schema, security, operator and independent review tasks.
2. Implement `scripts/live/db.py` with fixed SELECT queries, identifier quoting,
   admin visibility checks, bounded reads, timeouts, read-only startup and rollback.
3. Implement `scripts/live/verify.py`: validate plan/time inputs; check unique
   records/receipts, movements, balances, decisions, workflow, audit, self/foreign
   invariants, disabled-pack state and masked-egress metadata; emit a safe table.
4. Add `tests/unit/live/` synthetic fixtures and mutation tests; mock all DB access.
   Cover invalid intervals, missing evidence, duplicate effects and attempted writes.
5. Write README operator commands, prompt pack, plan example and evidence template.
   Check browser snippets statically only; record missing UI implementation honestly.
6. Run focused pytest, Ruff and strict mypy on new Python files; dry-run fixture CLI.
   Review changes independently; fix findings and rerun affected checks.
7. Commit, push and create the requested draft PR with proof and limitations.

External CLI reviewer attempts that time out must not be treated as reviews.
The initial Opus read-only review timed out; a bounded source-only retry is used.
REA's evidence-first reporting pattern and sklite's tenant-boundary pattern were
consulted locally; no sklite code was copied and no dependencies were added.
