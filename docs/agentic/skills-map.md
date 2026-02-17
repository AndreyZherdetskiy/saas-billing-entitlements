# Skills map — Billing Platform

Skills / subagents map. **Entry point:** [`AGENTS.md`](../../AGENTS.md). Product: [`spec.md`](../../spec.md).
Phases and execution contract: [`AGENTS.md` §10](../../AGENTS.md#10-stage-development-supplement).

| Task type | Skill / mechanism | Cursor Task |
|-----------|-------------------|-------------|
| Write / refine a plan | `superpowers:writing-plans` | parent agent |
| Product forks before code | `superpowers:brainstorming` | parent |
| Execute plan task-by-task | `superpowers:subagent-driven-development` | Orchestrator + subagents |
| Implement Task N | — | `generalPurpose` (Implementer) |
| Narrow shell / git / compose commands | — | `shell` |
| Search code / Spec | — | `explore` (quick / medium / very thorough) |
| Review a task (Gates A–D) | `superpowers:requesting-code-review` | `generalPurpose` Reviewer ≠ Implementer |
| Bugbot-like review | — | `bugbot` (only on explicit request) |
| Security (webhooks, keys, tenant, RBAC) | skill / role-prompt security | `security-review` or `generalPurpose` (Security) |
| Red test / bug | `superpowers:systematic-debugging` | `generalPurpose` or `shell` |
| Docs-grounding (official docs §5) | — | `generalPurpose` + WebFetch / WebSearch |
| ADR / AGENTS / runbooks / slo | — | `generalPurpose` (Docs) |
| Before “done” | `superpowers:verification-before-completion` | Orchestrator |
| Feature isolation | `superpowers:using-git-worktrees` | on human request |
| Finish a branch | `superpowers:finishing-a-development-branch` | only on request; **no push by default** |

## Roles → prompts

| Role | File |
|------|------|
| Orchestrator | [`role-prompts/orchestrator.md`](role-prompts/orchestrator.md) |
| Implementer | [`role-prompts/implementer.md`](role-prompts/implementer.md) |
| Reviewer | [`role-prompts/reviewer.md`](role-prompts/reviewer.md) |
| Grounding | [`role-prompts/grounding.md`](role-prompts/grounding.md) |
| Security | [`role-prompts/security.md`](role-prompts/security.md) |
| Docs | [`role-prompts/docs.md`](role-prompts/docs.md) |
| Test | [`role-prompts/test.md`](role-prompts/test.md) |

## Parallelism

Allowed **only** when the plan explicitly marks independent tracks with a sync point. Otherwise — strictly by `Depends on`.

## Subagent models

Cursor built-in models only (see user/global rule `subagent-builtin-models-only`); Implementer and Reviewer are separate Task invocations.
