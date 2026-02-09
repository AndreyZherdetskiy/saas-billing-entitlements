# Technical Specification

## Billing & Entitlements Platform for B2B SaaS

**Subtitle:** a single source of truth for subscriptions, plans, usage metering, and **entitlements**, with guaranteed event publication, immutable charge accounting, and financial reconciliation.


| Field              | Value                                                                                                      |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| Document version   | 3.2                                                                                                        |
| Date               | 09.02.2026                                                                                                 |
| Status             | Ready for implementation                                                                                   |
| Changelog 3.2      | Lean TZ trim: removed non-TZ narrative sections; consolidated demo walkthrough under §13; stripped non-product framing |
| Changelog 3.2 (2026-03-04) | ADR-015: API keys SHA-256 unique lookup (not a password KDF); §8.1 Bearer-verify NFR; profile E ramping-arrival-rate / `abortOnFail`; §4.3.5 snapshot key `ent:org:{id}:snapshot` (ADR-003 amendment); laptop overlay last hold 400 RPS evaluate (break 500) |
| Changelog 3.2 (2026-02-26, hit-path) | ADR-003 + ADR-015: per-process auth cache + snapshot L1; no DB session on full hit; skip tenant org SELECT when Bearer org matches body; laptop overlay last hold 1000 RPS evaluate (break 1500); §1.4 / §8.1 Stage 3 evaluate **3,000** RPS (≥3 replicas); usage **1,500**/s; cached evaluate p99 SLO **< 50 ms** |
| Changelog 3.2 (2026-03-07, prodlike) | `make perf-up` overlay (pool 2+1, relay×2): last hold **1500 RPS** evaluate / break **2000 RPS**; limiter SUT evaluate-path latency (CPU peg unproven); no application-code change on this overlay; [`docs/perf/2026-03-07-prodlike-hunt.md`](docs/perf/2026-03-07-prodlike-hunt.md) |
| Changelog 3.1      | ops: Kafbat UI (Stage 2); Prometheus/Grafana — ADR-013 **scoped Adopt** (LGTP profile `observability`, amended 2026-03-02); load testing (§10.4) |
| Implementation language | Python 3.12+                                                                                          |
| Dependency manager | `uv` (preferred) or Poetry                                                                                 |
| Target audience    | Middle+/Senior backend engineers, architect, DevOps, product owner, finance controller                     |


---



## 0. Glossary

Established English terms are introduced as **English term** with a plain-language definition. After first introduction in the glossary, accepted abbreviations are allowed: DLQ, OIDC, RBAC, JWT, SLA, SLO, NFR, API, CI/CD, IaC, SDK, ORM.


| Term | EN | Definition |
| ---- | -- | ---------- |
| Billing platform | Billing & Entitlements Platform | The product of this specification: plan catalog, subscription lifecycle, entitlement evaluation, usage metering, webhooks, transactional outbox, reconciliation; hereafter — **Platform** |
| Tenant organization / multi-tenant data isolation | multi-tenant / tenant | Data and entitlement isolation per B2B customer organization; all requests are filtered by organization identifier |
| Entitlement / plan-based access right | entitlement | Derived right for an organization to use a product feature with a limit, enforcement mode, and degradation policy |
| Entitlement evaluation | entitlement evaluation | Synchronous computation of allowed / denied / remaining quota from subscription, plan, usage, and overrides |
| Entitlement override | entitlement override | Manual (admin) change to a limit or permission for a specific organization outside the base plan |
| Product catalog | product catalog | Directory of products, plans, prices, and features publishable without deploying application code |
| Plan | plan | Versioned tariff configuration with billing interval, trial period, and entitlement policy |
| Price | price | Concrete price point for a plan (flat, per unit, tiered, metered) |
| Product feature | feature | Logical capability (`advanced_analytics`, `api_calls`, etc.) with type boolean / quota / rate_limit / seat |
| Subscription | subscription | Organization contract on a plan; state synchronized with the payment provider |
| Subscription item | subscription item | Subscription line tied to a price (e.g., seat quantity) |
| Trial period | trial | Temporary plan access until first successful payment |
| Grace period | grace period | Interval after failed payment while access is not fully revoked |
| Past due state | past_due | Subscription status after `invoice.payment_failed` until grace period ends |
| Access revocation | access revocation | Forced removal of entitlements after grace expiry or cancellation |
| Invoice | invoice | Financial document for a period: draft → open → paid / void / uncollectible |
| Invoice line item | invoice line item | Invoice line (flat, proration, usage) |
| Metered usage / usage metering | metered usage / usage metering | Charging by actual usage volume (API calls, seats, gigabytes) |
| Usage event | usage event | Atomic usage fact record with an idempotency key |
| Usage aggregate | usage aggregate | Hourly / period sum of events for quotas and invoicing |
| Period close | period close | Job that finalizes usage for a billing period and prepares invoice line items |
| Proration | proration | Top-up / credit calculation when changing plan mid-period |
| Monthly recurring revenue | MRR (Monthly Recurring Revenue) | Recurring revenue metric from active subscriptions |
| Revenue leakage / MRR leakage | revenue leakage / MRR leakage | Free usage with expired / unpaid subscription |
| Involuntary churn | involuntary churn | Customer loss due to payment failure without successful recovery |
| Dunning | dunning | Retry and notification cycle after failed payment; Stage 1 — domain events only; full cycle from Stage 2 |
| Payment provider | payment provider | External card and invoice system; in this project — Stripe-style mock |
| Payment provider port | PaymentProviderPort | Integration abstraction: domain does not depend on a specific Stripe SDK |
| Mock Stripe | mock Stripe | Separate service emulating Stripe Billing HTTP API and webhooks |
| Webhook | webhook | Provider HTTP callback on subscription / invoice status change |
| Webhook signature (HMAC) | webhook signature (HMAC) | HMAC-SHA256 verification of `Stripe-Signature` header with time tolerance |
| Idempotency | idempotency | Safe operation retry: same key → same result without double effect |
| Idempotency key | Idempotency-Key | Header / field uniquely identifying a business operation |
| Dual write | dual write | Anti-pattern: writing to DB and broker without a shared transaction |
| Transactional outbox | transactional outbox | Insert of domain changes and `outbox_messages` row in one DB transaction |
| Outbox relay | outbox relay | Separate process: poll unpublished rows → Kafka → `published_at` |
| Dead letter queue (DLQ) | dead letter queue (DLQ) | Store for poison / exhausted-retry messages for manual triage |
| Poison message | poison message | Message that consistently breaks the handler; goes to DLQ instead of blocking the stream |
| Billing event bus | billing event bus | Kafka topics with facts committed in PostgreSQL |
| Event envelope | event envelope | Wrapper: `schema_version`, `event_id`, `event_type`, `correlation_id`, `payload` |
| Partition key | partition key | Usually `organization_id`: event ordering within one tenant |
| At-least-once | at-least-once | Delivery may repeat; consumer must be idempotent |
| Exactly-once | exactly-once | End-to-end for webhooks is unrealistic; internally — idempotency + transactional outbox |
| Reconciliation | reconciliation | Nightly / manual comparison of Platform invoices and statuses ↔ mock Stripe; first-class subsystem |
| Reconciliation discrepancy | reconciliation discrepancy | Recorded mismatch: amount, status, missing entity |
| Immutable / auditable ledger | immutable / auditable ledger | Append-only financial entry log (charges, payments, credits, quota debits) with no UPDATE/DELETE by application code |
| Ledger entry | ledger entry | Immutable ledger line: debit/credit in cents or usage units, reference to invoice / subscription |
| Source of truth | source of truth | PostgreSQL for operational entitlement reads; Kafka — integration boundary |
| Primary DB node | primary | Sole PostgreSQL node accepting writes (subscriptions, outbox, webhooks, ledger) |
| Read replica | read replica | Async PostgreSQL copy for read-heavy paths (entitlement evaluation with acceptable eventual consistency, usage reports); writes forbidden |
| Table partitioning | table partitioning | Splitting a large table into sections (RANGE/LIST) without changing the logical model; in the Platform — `usage_events` by month |
| Sharding | sharding | Horizontal data split across multiple primary nodes; roadmap when primary write saturates, **not** Stage 1 (see §12.13) |
| Entitlement cache | entitlement cache | Organization entitlement snapshot in Redis with TTL and version invalidation |
| Cache invalidation | cache invalidation | Flush / version bump after webhook, plan change, override |
| CQRS (simplified) | CQRS (simplified) | Entitlement evaluation — synchronous read path; Kafka events — async integration |
| State machine | state machine | Allowed subscription / invoice status transitions |
| Rate limiting | rate limiting | Redis token bucket on API key and/or `rate_limit` feature type |
| Distributed lock | distributed lock | Lock via Redis / `FOR UPDATE SKIP LOCKED` for relay and schedulers |
| RBAC | RBAC | Roles `platform_admin`, `revops_read`, `product_service`, etc. |
| OAuth2 / OIDC | OAuth2 / OIDC | Stage 2: client credentials for service-to-service; Stage 1 — API keys |
| Audit trail / audit log | audit trail / audit log | Immutable history of financially significant actions (separate from ledger entries) |
| Correlation ID | correlation ID | End-to-end request ID in logs, traces, and events |
| UUIDv7 | UUIDv7 | Version 7 UUID with time ordering; default surrogate PK and `public_id` value (generated in app; on PostgreSQL 18+ `uuidv7()` is acceptable) |
| Dual-id / public ID | dual-id / public_id | Pair `id BIGINT IDENTITY` (PK, internal only) + `public_id` UUIDv7 UNIQUE (API, URL, events); only `public_id` exposed externally |
| Composite primary key | composite primary key | PK from two+ columns; in the Platform — only for pure M:N links without their own lifecycle; otherwise surrogate + UNIQUE |
| Natural key / UNIQUE | natural key / UNIQUE | Business uniqueness (`external_id`, `(plan_id, feature_id)`, idempotency key) via `UNIQUE`, not as PK shape |
| Structured logging | structured logging | JSON logs via structlog with context fields |
| Distributed tracing | distributed tracing | OpenTelemetry spans across HTTP, DB, Redis, Kafka |
| Liveness / readiness probe | liveness / readiness probe | Orchestrator probes: process alive / ready for traffic (DB, Redis, Kafka) |
| Graceful shutdown | graceful shutdown | Finish in-flight requests and jobs before SIGTERM within a time limit |
| HPA | HPA | Horizontal Pod Autoscaler by CPU/RPS (stub in Helm) |
| SLA / SLO | SLA / SLO | External commitments and internal targets: latency, webhook loss, reconciliation accuracy |
| SLI | SLI | Measurable quantity for SLO (share of successful entitlement evaluations, outbox lag, etc.) |
| NFR | NFR | Performance, reliability, security, observability |
| CI/CD | CI/CD | GitHub Actions: lint, types, tests, image build, deploy to staging |
| Definition of Done (DoD) | Definition of Done (DoD) | Acceptance checklist for product feature / stage |
| ADR | ADR | Short document “context → decision → consequences” |
| ORM | ORM | SQLAlchemy 2 async: explicit `select()`, no lazy-load in async |
| Alembic migration | Alembic migration | Versioned up/down PostgreSQL schema changes; zero-downtime plan — §8.9 |
| Docker Compose | Docker Compose | Single `compose up` for api, workers, relay, postgres, redis, kafka, mock-stripe, demo-ui; from Stage 2 — Kafbat UI |
| Kafbat UI | Kafbat UI | Lightweight OSS web UI for Kafka cluster (topics, messages, consumer lag); successor to Provectus kafka-ui (`ghcr.io/kafbat/kafka-ui`) |
| Load / performance test | load / performance test | Peak load simulation close to NFR §1.4 / §8.1 for several minutes; tool — k6 (preferred) or Locust |
| Prometheus / Grafana (optional) | Prometheus / Grafana | Metric collection and SLI visualization; Stage 3 — **scoped Adopt** via opt-in Compose profile `observability` ([ADR-013](docs/adr/013-prometheus-grafana.md)); default compose off |
| Kubernetes / Helm | Kubernetes / Helm | Minimal chart for api, worker, outbox relay |
| REST API / OpenAPI | REST API / OpenAPI | HTTP JSON API; specification generated by FastAPI |
| Batch ingest | batch ingest | `POST /usage/events/batch` up to 1000 events |
| Soft / hard enforcement | soft / hard enforcement | soft — warning; hard — deny; degraded — reduced mode |
| White-label | white-label | Ability to deliver the Platform as internal / brandable infrastructure |
| PCI DSS | PCI DSS | Card data storage delegated to provider; Platform does not store PAN |
| VAT / GST | VAT / GST | Out of scope for Stages 1–2 |
| Customer portal | customer portal | Billing UI for end customer — Stage 3, optional |
| Demo / admin UI | demo / admin UI | Thin frontend for demo: subscriptions, usage, reconciliation status; logic on backend |
| Staging / production | staging / production | Deployment environments |
| Runbook | runbook | Step-by-step SRE/on-call instruction for an alert |
| Test factory | test factory | Organization / Subscription generators for pytest |
| Contract test | contract test | Event schema and OpenAPI backward-compatibility checks |
| Testcontainers | testcontainers | PostgreSQL / Redis / Kafka in integration tests |



---



## Table of Contents

1. [Product charter and business pain](#1-product-charter-and-business-pain)
2. [Personas, roles, and user scenarios](#2-personas-roles-and-user-scenarios)
3. [Scope: in / out / stages 1–3](#3-scope-in--out--stages-13)
4. [Architecture](#4-architecture)
5. [Technology stack](#5-technology-stack)
6. [Domain model and data schema](#6-domain-model-and-data-schema)
7. [APIs and events](#7-apis-and-events)
8. [Non-functional requirements](#8-non-functional-requirements)
9. [Repository structure](#9-repository-structure)
10. [Testing strategy and CI/CD](#10-testing-strategy-and-cicd)
11. [Acceptance criteria](#11-acceptance-criteria)
12. [Architecture decisions](#12-architecture-decisions)
13. [Demo UI (thin frontend)](#13-demo-ui-thin-frontend)
14. [Appendices](#appendices)

---



## 1. Product charter and business pain



### 1.1. Name and positioning

**Product:** SaaS Billing & Entitlements Platform, hereafter — **Platform**.

**Positioning:** internal or white-label platform for B2B SaaS with **multi-tenant** data isolation: plan catalog, subscription lifecycle, usage metering, entitlement evaluation, Stripe-style **webhook** ingestion, reliable billing fact publication to Kafka via **transactional outbox**, immutable **ledger**, daily financial **reconciliation**, and payment recovery (**dunning**) from Stage 2.

The Platform **does not replace** full Stripe Billing out of the box and does not claim PCI DSS card storage. The Platform **closes** the critical gap between “money was charged by the provider” and “the customer actually gets what they pay for”: entitlements, limits, usage accounting, finance↔product reconciliation, and measurable reduction of MRR leakage and involuntary churn.

### 1.2. Problem (business pain)

A typical Series A–C B2B SaaS lives in three weakly aligned worlds:


| World | What breaks | Business impact |
| ----- | ----------- | --------------- |
| Product / engineering | Plan limits hardcoded; entitlement checks differ in five places | Enterprise sees “limit exceeded”; free tier gets premium features |
| Finance / RevOps | MRR in CRM ≠ Stripe revenue ≠ usage in the warehouse | Reports untrustworthy; disputes with investors and auditors |
| Customer Success | Renewal failed → grace not aligned → access revoked at wrong time | “We pay but were shut off” or “We don’t pay but it still works” |


**Incidents the Platform prevents:**

1. **Stale limits after upgrade.** Customer upgraded plan; `invoice.paid` webhook delayed 40 seconds; API rejected hundreds of requests at old limit → support ticket, escalation, churn risk.
2. **Renewal failure without dunning.** Card expired; subscription `past_due` but Redis entitlement cache still grants full access for a day → revenue leakage; or customer paid by wire, system unaware → false block; without dunning cycle involuntary churn grows.
3. **Usage vs invoice mismatch.** Usage DB shows 1.2M API calls, invoice line shows 980K → thousands of dollars under-billed per month on one enterprise account; without reconciliation and ledger there is no provable convergence.



### 1.3. Target business metrics (product KPIs)

The Platform is measured by customer impact, not HTTP endpoint count.


| Metric | Baseline (before Platform) | Target (after 6 mo.) | How we measure | Architecture link |
| ------ | -------------------------- | -------------------- | -------------- | ----------------- |
| MRR leakage on invalid subscription | 1.5–3% MRR | < 0.2% MRR | Active entitlements outside `active`/`trialing` × ARPU | Evaluation from subscription status + short cache TTL + revoke after grace |
| Involuntary churn after payment failure | 8–12% annual | < 5% annual | Cohort: cancel within 30 days after `invoice.payment_failed` | Grace period + dunning (Stage 2+) + CS events |
| Reconciliation accuracy (finance vs platform) | 85–92% match | ≥ 99.5% | Nightly run: invoice line totals vs mock Stripe registry + ledger | First-class reconciliation + immutable entries |
| Entitlement evaluation p99 latency | 50–200 ms (scattered) | < 50 ms (cache), < 80 ms (no cache) | Span `entitlement.evaluate` | Redis snapshot + PostgreSQL as source of truth |
| Webhook processing SLA | best-effort; 5–15% loss on deploy | 99.9% processed < 60 s; 0% loss (persist + outbox) | `webhook_events.status`, `outbox_messages.published_at` | Persist-first + outbox + graceful shutdown |
| “Wrong plan / limit” tickets | 15–25 / mo. per 500 accounts | < 3 / mo. | Support tag `billing-entitlement` | Single evaluator + demo UI for CS |
| Time to launch new plan | 2–5 person-days (code) | < 2 hours (config + publish) | Product ops workflow | Catalog without code deploy |




### 1.4. Target scale

Figures drive NFR and capacity planning. Stage 1 need not hold full Stage 3 scale, but architecture must not require a rewrite.


| Parameter | Stage 1 | Stage 2 | Stage 3 |
| --------- | ------- | ------- | ------- |
| Tenant organizations | 50 | 500 | 5,000 |
| Active subscriptions | 200 | 2,000 | 20,000 |
| Entitlement evaluations / sec (peak) | 100 | 1,000 | 3,000 |
| Usage events / sec | 50 | 500 | 1,500 |
| Webhooks / day | 500 | 5,000 | 50,000 |
| Kafka messages / day | 10K | 100K | 1M |
| PostgreSQL (hot data) | 5 GB | 50 GB | 500 GB |
| Invoice line items / month | 500 | 5,000 | 50,000 |
| Ledger entries / month | 2K | 25K | 300K |




### 1.5. Platform responsibility boundaries

**Platform is responsible for:**

- product, plan, price, feature, and entitlement catalog;
- subscription lifecycle and mock Stripe synchronization;
- entitlement evaluation and Redis caching;
- usage ingestion and aggregation;
- immutable ledger for charges / payments / credits;
- billing event generation and Kafka publication via transactional outbox;
- webhook ingestion, idempotent processing, first-class reconciliation;
- dunning cycle (events Stage 1; orchestration Stage 2+);
- Admin API and internal API for product services;
- audit log for financially significant operations;
- thin operator demo UI.

**Platform is not responsible for:**

- card data storage (PCI DSS) — delegated to provider;
- full customer portal — intentionally deferred to Stage 3 (optional);
- tax calculation (VAT/GST) — out of scope Stages 1–2;
- dunning email UX (template render, ESP) — integration events and retry orchestration only; email delivery — Kafka consumer;
- BI warehouses and CRM — Kafka consumers / exports.

---



## 2. Personas, roles, and user scenarios



### 2.1. Personas



#### P1: RevOps Manager (Anna)

- **Goal:** MRR dashboard matches bank and provider; explain discrepancies quickly.
- **Pain:** “MRR dropped $12K yesterday — churn or failed payment?”
- **Tools:** Admin API, reconciliation reports, billing events in warehouse, demo UI reconciliation runs.



#### P2: Product backend engineer (Dmitry)

- **Goal:** one API call for `can_use_feature(org_id, "advanced_analytics")`.
- **Pain:** four different places check limits and disagree.
- **Tools:** entitlements API, webhook / event consumer examples.



#### P3: Finance Controller (Elena)

- **Goal:** month close without manually reconciling hundreds of invoice lines; provable ledger.
- **Pain:** usage billing does not match contract.
- **Tools:** reconciliation cron output, CSV export, audit log, ledger entries.



#### P4: Customer Success Manager (Igor)

- **Goal:** see subscription status, entitlement snapshot, and dunning step on escalation.
- **Pain:** “Customer says they paid, system says past_due.”
- **Tools:** read-only Admin API, subscription event timeline, demo UI.



#### P5: Platform SRE (Olga)

- **Goal:** deploy without losing webhooks; alerts on stuck outbox; runbooks.
- **Pain:** after release some events land in DLQ.
- **Tools:** OpenTelemetry, Grafana (when adopted per ADR), outbox lag metrics, **runbooks**.



### 2.2. Roles and access control (RBAC)


| Role | Description | Permissions |
| ---- | ----------- | ----------- |
| `platform_admin` | Full access | CRUD plans/prices/features; webhook replay; manual overrides; manual reconciliation trigger |
| `revops_read` | Finance read-only | Subscriptions, invoices, reconciliation runs, ledger entries |
| `product_service` | Service-to-service | Evaluate entitlements, record usage, read subscription status |
| `webhook_ingest` | Provider adapter | Only webhook `POST`, scoped signing secret |
| `support_read` | CS read-only | Subscription and entitlement snapshot; minimal PII |
| `dunning_operator` | Stage 2+ | View/pause dunning campaign (no plan change) |


**Authentication:** Stage 1 — API keys: persist SHA-256 hex of the raw key and authenticate by unique `key_hash` lookup (not a password KDF; ADR-015) + webhook HMAC signatures. Stage 2 — OAuth2 client credentials for product services.

### 2.3. User scenarios



#### J1: New B2B customer — signup → trial → paid

```
[Product App] → create organization
    → [Platform] POST /v1/organizations (idempotent)
    → [Mock Stripe] create customer
    → [Platform] POST /v1/subscriptions { plan: "pro_trial", trial_days: 14 }
    → webhook subscription.created
    → processing → outbox → Kafka: subscription.trial_started
    → ledger entry (trial_grant, 0 cents) + entitlement evaluation → Redis cache
    → [Product App] GET /v1/organizations/{org_id}/entitlements

Day 14:
    → invoice.created, invoice.paid (if card OK)
    → subscription.active → ledger (invoice_paid) → entitlement refresh
    → Kafka: subscription.activated
```

**Failure mode:** `invoice.paid` delay. Product **must not** rely on webhook alone: entitlements API reads PostgreSQL as source of truth; status cache TTL ≤ 60 s.

#### J2: Usage metering → invoice line item

```
[Product API Gateway] → POST /v1/usage/events (batch)
    → verify org + subscription status (or “allow write” flag)
    → insert usage_events (monthly partitions)
    → background worker: hourly aggregates
    → period end: usage.close_period
    → draft invoice_line_item + ledger (usage_charge)
    → Kafka: usage.period_closed
    → sync with mock Stripe → reconciliation cron
```



#### J3: Renewal failure → grace → dunning → block

```
invoice.payment_failed
    → subscription.past_due
    → grace_period_days policy from plan
    → Kafka: subscription.payment_failed
    → entitlements: degradation mode (configurable per feature)
    → [Stage 2+] dunning campaign: attempts day 1/3/7 + notification events

Grace expired (and dunning without success):
    → background worker: subscription.enforce_grace_expiry
    → status = unpaid → revoke entitlements + ledger (access_revoked_marker)
    → Kafka: subscription.access_revoked
```



#### J4: Monthly financial reconciliation (first-class)

```
[Scheduler 02:00 UTC] reconciliation.run
    → list mock Stripe invoices for period
    → compare with Platform invoices + line items + ledger entry totals
    → reconciliation_runs + discrepancies
    → on mismatch: alert + Kafka: reconciliation.mismatch
    → runbook: classify → webhook replay / manual compensating entry (append-only)
```



#### J5: Mid-cycle plan change

```
POST /v1/subscriptions/{id}/change-plan
    → proration calculation (stub / mock logic)
    → call mock Stripe
    → webhooks subscription.updated, invoice.created
    → ledger (proration_debit / proration_credit)
    → entitlement refresh (policy: immediate | end_of_period)
    → Kafka: subscription.plan_changed
```

---



## 3. Scope: in / out / stages 1–3



### 3.1. In scope (overall)

- multi-tenant data isolation;
- catalog: products, plans, prices (recurring + metered);
- features and entitlements tied to plans;
- subscription state machine;
- mock Stripe adapter (HTTP API + webhooks) with idempotency;
- transactional outbox → relay → Kafka;
- immutable ledger;
- usage ingestion and aggregation;
- entitlement evaluation with Redis cache;
- webhook / invoice / ledger reconciliation as first-class;
- dunning: events Stage 1, orchestration Stage 2+;
- Admin + Internal HTTP API (FastAPI);
- secrets outside git, rate limiting, health checks, graceful shutdown;
- SLO/SLI, alerts, runbooks;
- zero-downtime migration plan;
- Alembic migrations, Docker Compose;
- minimal Helm chart;
- observability: structlog + baseline OpenTelemetry;
- CI: GitHub Actions (lint, typecheck, test, build);
- thin demo admin UI (see §13);
- Kafka ops UI (Kafbat) in local Compose from Stage 2;
- load campaign per NFR at end of Stage 3 (§8.1.1 / §10.5);
- Prometheus/Grafana evaluation at Stage 3 (§8.5.1).



### 3.2. Out of scope

- production integration with live Stripe (mock + port abstraction only);
- customer-facing billing portal (Stage 3, optional);
- taxes, multi-currency FX (Stage 1 — USD only);
- SOC2 package;
- mobile SDK;
- GraphQL API;
- multi-region active-active;
- full PDF invoice rendering;
- owned ESP for dunning emails (events for external sender only).



### 3.3. Stage 1 — Foundation (MVP, 8–10 weeks)

**Goal:** end-to-end happy path: organization → trial subscription → entitlements → webhook → outbox → Kafka; minimal ledger and reconciliation skeleton.


| Component | Deliverables |
| --------- | ------------ |
| Domain | organizations, products, plans, prices, features, plan_features |
| Subscriptions | create, cancel, status sync via webhooks |
| Mock Stripe | customers, subscriptions, invoices, webhook signatures |
| Transactional outbox | table + relay process |
| Kafka | 5+ base topics, JSON schema v1 |
| Entitlements | evaluate + Redis + DB fallback |
| Ledger | append-only entries for activation / payment / revoke (minimum) |
| Reconciliation | manual Admin trigger + discrepancies table (daily cron — Stage 2) |
| Dunning | only `subscription.payment_failed` / `past_due` events (no retry orchestration) |
| API | Internal + Admin CRUD |
| Operations | `/health/live`, `/health/ready`, graceful shutdown, secrets from env |
| Demo UI | organization / subscription / entitlements / webhook status screens |
| Infra | Compose, Alembic, unit + integration pytest |
| CI | Ruff, mypy, tests on PR |


**Stage 1 NFR:** 50 tenant organizations, 100 evaluations/sec, zero webhook loss after persist.

### 3.4. Stage 2 — Usage, reconciliation, and dunning (6–8 weeks)


| Component | Deliverables |
| --------- | ------------ |
| Usage | ingest, hourly aggregates, period close |
| Invoicing | invoices + line items, sync with mock Stripe |
| Ledger | full entry types: usage_charge, proration, credit, dunning_fee (if applicable) |
| Reconciliation | daily cron, discrepancies, alerts, ledger ↔ invoices ↔ mock Stripe |
| Dunning | campaigns: retry schedule, statuses, notification events, operator pause |
| Background workers | aggregates, period close, grace, reconciliation, dunning steps |
| Grace | policy engine for past_due |
| Plan change | upgrade/downgrade + stub proration |
| UI | usage charts, reconciliation runs / discrepancies, dunning card |
| Kafka UI | **Kafbat UI** in Docker Compose (local/demo): topics, message browse, consumer lag; not public prod without auth |
| PostgreSQL | RANGE partitions on `usage_events` by month required; writes still on primary |
| Observability | OTel dashboards, SLO alerts, runbooks |
| Migrations | proven zero-downtime plan for “hot” tables (including partition detach/attach) |




### 3.5. Stage 3 — Scale and hardening (6–8 weeks)


| Component | Deliverables |
| --------- | ------------ |
| Performance | **read replica** for evaluate (if eventual consistency acceptable) and usage reports; cache warming; pools; PgBouncer |
| Writes | all mutations (`subscriptions`, `outbox_messages`, `webhook_events`, ledger) — **primary only** |
| Security | key rotation, audit export, stronger rate limiting |
| K8s | Helm: api, worker, outbox relay, consumer |
| HA | relay leader election, DLQ replay tooling |
| Sharding | **not implemented**; transition criteria and ADR “no sharding in Stage 1” (§12.13) |
| Advanced entitlements | boolean + quota + rate + seat |
| Metrics (decision) | ADR-013 **scoped Adopt** — LGTP stack (`deploy/observability/`, profile `observability`); Mimir / production object storage deferred |
| Load (closure) | load test campaign per §10.4 / profiles §8.1.1 — **mandatory** end-of-project DoD (after Stage 3 features) |
| Documentation | OpenAPI, ADR, runbooks, load report (`docs/perf/…`) |


No separate “Stage 4”: Kafka ops UI closes in Stage 2; Prometheus/Grafana — gate in Stage 3; load — final Stage 3 / project criterion.


---



## 4. Architecture



### 4.1. System context (C4 Level 1)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         B2B SaaS ecosystem                                   │
│                                                                              │
│  ┌──────────────┐   REST    ┌──────────────────────────────────────┐      │
│  │ Product App  │──────────▶│  Billing & Entitlements Platform      │      │
│  │ (customer    │◀──────────│                                      │      │
│  │  SaaS)       │  responses└───────────┬──────────────────────────┘      │
│  └──────────────┘                       │ billing events                    │
│                                          ▼                                   │
│  ┌──────────────┐              ┌──────────────────────┐                   │
│  │ RevOps /     │  Admin REST  │   Apache Kafka         │                   │
│  │ Finance /    │─────────────▶│   (fact bus)           │                   │
│  │ Demo Admin UI│              └──────────┬───────────┘                   │
│  └──────────────┘                         ▼                                   │
│                                ┌──────────────────────┐                   │
│                                │ Warehouse / BI /       │                   │
│                                │ analytics / dunning    │                   │
│                                │ notifier               │                   │
│                                └──────────────────────┘                   │
│                                                                              │
│  ┌──────────────┐   webhooks + API                                          │
│  │ Mock Stripe  │◀───────────────────────────────────────────────────────│
│  │ (provider    │──────────────────────────────────────────────────────────▶│
│  │  mock)       │                                                           │
│  └──────────────┘                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**External actors:** Product App (entitlement and usage consumer), RevOps/Finance, Mock Stripe, operators/SRE, demo admin UI.

### 4.2. Containers (C4 Level 2)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              Billing & Entitlements Platform                                 │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐    │
│  │  billing-api    │  │  billing-worker │  │  outbox-relay           │    │
│  │  (FastAPI)      │  │  (Celery)       │  │  (separate process)     │    │
│  │  REST, webhooks,│  │  aggregates,    │  │  poll outbox → Kafka    │    │
│  │  entitlements   │  │  period close,  │  │  idempotent producer    │    │
│  │                 │  │  grace, recon,  │  │                         │    │
│  │                 │  │  dunning        │  │                         │    │
│  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────┘    │
│           └────────────────────┼────────────────────────┘                   │
│           ┌────────────────────┼────────────────────┐                       │
│           ▼                    ▼                    ▼                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐           │
│  │  PostgreSQL     │  │  Redis          │  │  Apache Kafka   │           │
│  │  (source of     │  │  entitlement    │  │  event bus      │           │
│  │   truth +       │  │  cache, locks,  │  │                 │           │
│  │   ledger)       │  │  rate limits    │  │                 │           │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘           │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │  mock-stripe    │  │  demo-admin-ui  │  │  kafbat-ui      │───────────►│
│  │  (FastAPI)      │  │  (SPA → API)    │  │  (Stage 2+: browse Kafka)│   │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘            │
└─────────────────────────────────────────────────────────────────────────────┘
```

`kafbat-ui` (Stage 2+) reads the same Kafka broker (topics, messages, consumer lag). Optionally in Stage 3 (ADR-013 **scoped Adopt**): LGTP profile `observability` for metrics §8.5 — **not** on by default for Stages 1–2.



### 4.3. Key architectural decisions (summary; detail — §12)



#### 4.3.1. Kafka as integration boundary

Operational entitlement reads **do not** go through Kafka. Kafka carries **facts** after PostgreSQL commit for warehouses, RevOps automation, dunning notifier service.


| Criterion | Kafka | Alternative (NOTIFY / Redis Streams) |
| --------- | ----- | ------------------------------------ |
| Durability | replicated log, retention | PG NOTIFY not for streaming at scale |
| Multiple consumers | consumer groups, replay | NOTIFY — effectively one consumer |
| Analytics contract | industry standard | ad-hoc |
| Backpressure | lag monitoring | Redis — memory bound |




#### 4.3.2. Transactional outbox vs dual write

Problem: `UPDATE subscriptions` + separate `kafka.publish` — either downstream never learns of activation, or gets a false event on transaction rollback.

**Solution.** In one transaction — domain tables + `INSERT INTO outbox_messages`; separate `outbox-relay` process publishes and sets `published_at`. CDC (Debezium) rejected in Stage 1 due to operational complexity: outbox gives explicit domain events with schema versioning.

#### 4.3.3. Outbox relay design

```
┌──────────────┐   one TX     ┌──────────────────┐
│ Webhook      │ ───────────▶ │ PostgreSQL       │
│ Handler      │              │ subscriptions    │
│              │              │ webhook_events   │
│              │              │ ledger_entries   │
│              │              │ outbox_messages  │
└──────────────┘              └────────┬─────────┘
                                       │ FOR UPDATE SKIP LOCKED
                                       ▼
                              ┌──────────────────┐
                              │ outbox-relay     │
                              │ batch → Kafka    │
                              │ → published_at   │
                              └────────┬─────────┘
                                       ▼
                              ┌──────────────────┐
                              │ billing.* topics │
                              └──────────────────┘
```

**Key** `outbox_messages` **fields:** `id` BIGINT PK (also Kafka message key; no `public_id`), `aggregate_type`, `aggregate_id`, `event_type`, `payload` JSONB, `idempotency_key`, `partition_key` (usually internal `organization_id`), `created_at`, `published_at`, `publish_attempts`, `last_error`.

**Relay algorithm:**

1. Select up to 100 rows `published_at IS NULL AND publish_attempts < 10` with `FOR UPDATE SKIP LOCKED`.
2. Publish: key=`id`, headers=`event_type`, `schema_version`.
3. Success → `published_at=now()`.
4. Error → increment attempts, `last_error`, exponential backoff (**retry**).
5. Attempts ≥ 10 → `outbox_dead_letters` + alert.

**Ordering:** within tenant by `partition_key`. **Delivery:** at-least-once; consumers idempotent.

#### 4.3.4. Immutable ledger

**Why (KPI link):** reconciliation accuracy ≥ 99.5% and month close without “manual history edits.” Mutating amounts in `invoices` without auditable history erodes Finance trust.

**Rules:**

- `INSERT` only; application code does not `UPDATE`/`DELETE` `ledger_entries` rows;
- corrections — compensating entries (reversal) only with `reverses_entry_id`;
- every financially significant operation (payment, usage charge, proration, credit, revoke marker) writes a ledger entry in the same transaction as domain change / outbox row;
- reconciliation compares ledger aggregates with invoice line totals and mock Stripe registry.



#### 4.3.5. Entitlement evaluation flow

```
POST /v1/entitlements/evaluate
  → auth: L1 sha256_hex → AuthContext (TTL AUTH_CACHE_TTL_SECONDS, default 2 s)
       MISS → SHA-256(bearer) + indexed SELECT api_keys (ADR-015); populate L1
  → tenant context
       matching tenant (ctx.organization_public_id == body.organization_public_id):
         use ctx.organization_id — no SELECT organization
       platform_admin: load organization when required
  → snapshot L1 organization_id → dict (TTL ENTITLEMENT_L1_TTL_SECONDS, default 1 s)
       HIT → snapshot (no Redis GET)
       MISS → Redis GET ent:org:{org_id}:snapshot
            HIT → snapshot (TTL 30–60 s; version key feeds HTTP `version`)
            MISS → subscription + plan + plan_features + usage_aggregates (session required)
  → status policy (active/trialing / past_due+grace / unpaid)
  → feature type: boolean | quota | rate | seat
  → cache write (L1 + Redis on miss path), latency metric, response
```

**Full hit (tenant, auth L1 + snapshot L1):** no Postgres session, no Redis GET, no org SELECT (ADR-003 + ADR-015).

Evaluation is **read-only**. Usage increment — separate `POST /v1/usage/events`.

**Invalidation:** after webhook / override / plan publish, bump the version key `ent:org:{org_id}:version`, **delete** `ent:org:{org_id}:snapshot`, and **drop snapshot L1** in the handling process (ADR-003). Auth cache: invalidate on rotate/revoke by `api_key_id` (and old digest on rotate when known) (ADR-015). The version key remains for the HTTP `version` field.

#### 4.3.6. Reconciliation as first-class

Schedule: Celery Beat `0 2 * * *` (from Stage 2) + manual Admin trigger (from Stage 1).

1. Create `reconciliation_run` (status=running).
2. Pull mock Stripe invoices for period.
3. Match by `external_invoice_id`: missing_in_platform, amount_mismatch, status_mismatch, missing_in_stripe.
4. Compare ledger entry totals with invoice totals and usage line items.
5. Record discrepancies; alert if delta > $100; outbox: `reconciliation.mismatch`.
6. Re-run for same period creates new run and **does not** mutate invoices / ledger.



#### 4.3.7. Dunning — Stage 2+


| Day after payment_failed | Action | Event |
| ------------------------ | ------ | ----- |
| 0 | past_due + degraded entitlements | `subscription.payment_failed` |
| 1 | retry charge via mock Stripe + “remind” event | `dunning.attempt_scheduled` |
| 3 | second attempt | `dunning.attempt_failed` / `succeeded` |
| 7 | final attempt / CS escalation | `dunning.final_notice` |
| grace_end | unpaid + revoke | `subscription.access_revoked` |


Orchestration in background worker; email delivery — external Kafka consumer (outside Platform scope). KPI link: involuntary churn < 5%.

#### 4.3.8. PostgreSQL scaling: primary, read replica, partitions (Stages 2–3)

**Stages 2–3 goal:** relieve primary pressure on read-heavy and time-series paths **without** early sharding.


| Path | Node | Consistency | Stage |
| ---- | ---- | ----------- | ----- |
| Writes: subscriptions, outbox, webhooks, ledger | **primary** | strong (single TX) | 1+ |
| Ingest `usage_events` | **primary** (partitioned table) | strong | 2+ |
| `entitlement.evaluate` (on Redis miss) | primary (Stages 1–2) → **read replica** (Stage 3, if product accepts replica lag) | eventual on replica | 3 |
| Usage reports / aggregates for Admin UI | **read replica** | eventual | 3 |
| Reconciliation, dunning steps, relay | **primary** | strong | 2+ |


**Partitioning** `usage_events`**: `PARTITION BY RANGE (recorded_at)` with monthly sections (`usage_events_YYYY_MM`). Quota and period-close queries target relevant partitions; old months — detach/archive without blocking hot path. LIST by `organization_id` **not** used Stages 1–3 (hot tenants → skew).

**Sharding:** out of scope Stages 1–3. Considered only when **write** primary is sustainably saturated (WAL/IOPS/CPU, replica lag from write volume not heavy reports), after partitions + replica + pools exhausted. Until then — ADR “no sharding” (§12.13).

```
                    ┌─────────────────────┐
  writes ──────────▶│ PostgreSQL primary  │──▶ WAL ──▶ read replica(s)
  (subs, outbox,    │ + usage_events      │            ▲
   webhooks, ledger)│   RANGE by month    │            │ reads:
                    └─────────────────────┘            │  evaluate (opt.)
                                                       │  usage reports
```



### 4.4. Failure modes and mitigation


| Failure | Impact | Detection | Mitigation |
| ------- | ------ | --------- | ---------- |
| Duplicate webhook | double transition | UNIQUE `provider_event_id` | idempotent handler |
| Webhook lost before Platform | stale subscription | reconciliation cron | mock Stripe retention + replay API |
| Outbox relay down | downstream delay | `outbox_lag_seconds` | HA + SKIP LOCKED; alert > 5 min |
| Kafka unavailable | growing outbox backlog | producer errors | retries; storage in PG |
| Redis down | higher DB load | health | circuit → direct DB |
| Primary DB down | full write outage; evaluate on replica — stale data | `/health/ready` | restart primary; Stage 3: read replica read-only, no write failover without runbook |
| Replica lag | stale evaluate/reports | `replica_lag_seconds` | threshold: evaluate → fallback primary; alerts |
| Clock skew | grace / dunning errors | NTP | business rules on DB `now()` |
| Entitlement cache split-brain | wrong limits 30–60 s | version counter | short TTL + bump |
| Usage stream | write pressure / table growth | rate limit per org; RANGE partitions | 429; detach old partitions; buffer Stage 3 |
| Poison webhook | crash loop | error rate | quarantine `failed` + manual replay |
| Proration bug | revenue mismatch | reconciliation + ledger | feature flag; discrepancy report |
| Pod kill mid-request | lost in-flight | SIGTERM without drain | graceful shutdown + persist-first webhooks |


---



## 5. Technology stack

Stack is intentionally narrow: each item ties to KPI (MRR, churn, reconciliation accuracy, entitlement p99). No “zoo for fashion.”


| Technology | Version (target) | Role |
| ---------- | ---------------- | ---- |
| Python | 3.12.x | Backend language |
| FastAPI | 0.141.x | HTTP API, OpenAPI, DI |
| Uvicorn | 0.52.x | ASGI server; graceful shutdown |
| Pydantic | v2.13.x (v2 `<3`) | DTO, event schemas, Settings |
| SQLAlchemy | 2.0.x async | ORM + Core, AsyncSession |
| asyncpg | 0.31.x | PostgreSQL driver |
| Alembic | 1.19.x (`>=1.14,<2`) | Migrations (reversible; zero-downtime plan — §8.9) |
| PostgreSQL | 16.x (image 16.15) | Source of truth + ledger |
| Redis | 8.10.x | Entitlement cache, locks, rate limits |
| Celery | 5.6.x | Background jobs (aggregates, reconciliation, dunning, grace) |
| Apache Kafka | 4.3.x (KRaft) | Billing event bus; retention ≥ 30 days |
| confluent-kafka / aiokafka | aiokafka 0.14.x (this repo) | Producer in relay; consumer examples |
| structlog | 26.x | JSON logs |
| OpenTelemetry | 1.44.x (instrumentation 0.65b) | Traces and metrics (baseline) |
| Kafbat UI | latest stable (compose) | Kafka web UI (Stage 2+): topics, messages, lag; image `ghcr.io/kafbat/kafka-ui` |
| Prometheus + Grafana | latest LTS (optional) | Stage 3: only after ADR Adopt; scrape OTel/exporter metrics §8.5 |
| k6 (or Locust) | stable | Load scenarios §10.4 (end of Stage 3) |
| pytest + pytest-asyncio | 8.x / 0.24.x | Tests |
| testcontainers | 4.x | PG/Redis/Kafka in integration |
| httpx | 0.28.x | HTTP client in tests and mock Stripe client |
| Ruff | 0.8.x | Lint + format |
| mypy | 1.13.x | Strict typing |
| uv | 0.5.x | Dependencies and lockfile |
| Docker Compose | v2 | Local environment |
| Helm | 3.x | Minimal K8s chart |
| GitHub Actions | — | CI/CD |
| Demo UI (standard) | Vite + React + TypeScript | Thin SPA: display and OpenAPI calls only; HTML templates not the primary option |



### 5.1. Mock Stripe adapter

Separate `mock-stripe` service (FastAPI):

- API subset: `/v1/customers`, `/v1/subscriptions`, `/v1/invoices`, `/v1/invoiceitems`;
- webhook HMAC-SHA256 signature compatible with `Stripe-Signature`;
- state in separate tables or SQLite (isolated from Platform DB);
- deterministic fixtures: card decline, **retry** schedule for dunning scenarios;
- webhook redelivery API for reconciliation demo.

In the Platform — `PaymentProviderPort` abstraction: Stage 3 may swap mock → live Stripe without rewriting domain.

---



## 6. Domain model and data schema



### 6.1. Bounded contexts

1. **Tenant** — organizations, api_keys
2. **Catalog** — products, plans, prices, features, plan_features
3. **Subscription** — subscriptions, subscription_items, subscription_events
4. **Billing** — invoices, invoice_line_items (credit notes — Stage 2)
5. **Ledger** — ledger_entries (append-only)
6. **Usage** — usage_events, usage_aggregates_hourly, usage_periods
7. **Entitlement** — entitlement_overrides (optional materialized snapshots)
8. **Integration** — webhook_events, outbox_messages, reconciliation_*
9. **Dunning** — dunning_campaigns, dunning_attempts (Stage 2+)
10. **Audit** — audit_log



### 6.2. Identifier and key policy


| Entities | Key policy |
| -------- | ---------- |
| `organizations`, `subscriptions`, `invoices`, `usage_events`, public `ledger_entries` | **dual-id:** `id BIGINT GENERATED AS IDENTITY` PK + `public_id` UUIDv7 UNIQUE; externally (API / URL / OpenAPI / events) — only `public_id`; internal FK — `BIGINT id` |
| `products`, `plans`, `prices`, `features` | UUIDv7 PK |
| `plan_features` | surrogate UUIDv7 PK + `UNIQUE (plan_id, feature_id)` — **not** composite PK (has `limit_value`, `is_enabled`, `enforcement_mode`) |
| `webhook_events`, `reconciliation_*`, `dunning_*` | UUIDv7 PK |
| `outbox_messages` | BIGINT PK, **no** `public_id` (append-only, not exposed; Kafka key = `id`) |
| Other reference / operational (`api_keys`, overrides, etc.) | UUIDv7 PK; FK to dual-id entity — internal `BIGINT` |


Natural keys (`external_id`, idempotency keys, `(product_id, key, version)`) are `UNIQUE`, not PK shape. Composite primary key is not used in the Platform: `plan_features` has its own attribute lifecycle, hence surrogate + plan–feature uniqueness. Dual-id only on write-heavy / financially sensitive entities with public API — second index and sequential id leak risk offset by insert locality and compact FK; catalog and operational journals stay single-column UUIDv7. **Outbox** intentionally BIGINT without `public_id`: rows not exposed to clients; monotonic key convenient as Kafka message key. API and OpenAPI paths use `public_id`; services map public→internal at boundary. Sequential `id` never in DTO.

### 6.3. Core tables



#### `organizations`


| Column | Type | Notes |
| ------ | ---- | ----- |
| id | BIGINT IDENTITY PK | internal; FK inside DB |
| public_id | UUIDv7 UNIQUE | API / URL / events |
| external_id | VARCHAR UNIQUE | Product App ID (natural key) |
| name | VARCHAR | |
| billing_email | VARCHAR | |
| metadata | JSONB | |
| created_at / updated_at | TIMESTAMPTZ | |
| deleted_at | TIMESTAMPTZ NULL | soft delete |


Indexes: `external_id`, `created_at`; UNIQUE on `public_id`.

#### `api_keys`


| Column | Type | Notes |
| ------ | ---- | ----- |
| id | UUIDv7 PK | |
| organization_id | BIGINT FK NULL | → `organizations.id`; null = platform-wide |
| key_hash | VARCHAR(64) UNIQUE | SHA-256 hex of the raw key (lowercase, 64 chars); Alembic/ORM `String(64)` → PostgreSQL `VARCHAR(64)`; never bcrypt/argon2 for API keys (ADR-015) |
| key_prefix | VARCHAR(8) | display-only for logs; **not** unique; not used for authenticate |
| role | ENUM | platform_admin, product_service, … |
| expires_at / revoked_at | TIMESTAMPTZ NULL | |




Indexes: UNIQUE on `key_hash`; `key_prefix` may be indexed for logs but is **not** unique and is **not** the authenticate path.

#### `products` / `plans` / `prices` / `features` / `plan_features`

`products`**: UUIDv7 PK; `key` UNIQUE (`core_api`), `name`, `description`, `is_active`.

`plans`**: UUIDv7 PK; FK to product; `key` (`pro`, `enterprise`); `billing_interval` month|year; `trial_days`; `grace_period_days` (default 7); `dunning_policy` JSONB (Stage 2: retry schedule); `entitlement_policy` JSONB; `version`; `published_at` NULL = draft. Unique `(product_id, key, version)`.

`prices`**: UUIDv7 PK; FK plan; `currency` CHAR(3)=USD; `unit_amount_cents`; `pricing_model` flat|per_unit|tiered; `metered_feature_key`; `external_price_id`; `is_active`.

`features`**: UUIDv7 PK; `key` UNIQUE; `feature_type` boolean|quota|rate_limit|seat; `default_limit`; `reset_interval` hour|day|month|billing_period.

`plan_features`**: UUIDv7 PK (surrogate); `UNIQUE (plan_id, feature_id)`; `limit_value`; `is_enabled`; `enforcement_mode` hard|soft|degraded. Composite PK forbidden: row carries lifecycle attributes, not pure M:N.

#### `subscriptions`


| Column | Type | Notes |
| ------ | ---- | ----- |
| id | BIGINT IDENTITY PK | internal |
| public_id | UUIDv7 UNIQUE | API / URL / events |
| organization_id | BIGINT FK | → `organizations.id` |
| plan_id | UUIDv7 FK | → `plans.id` |
| status | ENUM | trialing, active, past_due, canceled, unpaid, incomplete |
| current_period_start / end | TIMESTAMPTZ | |
| cancel_at_period_end | BOOLEAN | |
| canceled_at / trial_end | TIMESTAMPTZ NULL | |
| external_subscription_id | VARCHAR UNIQUE | |
| idempotency_key | VARCHAR UNIQUE | |
| metadata | JSONB | |
| created_at / updated_at | TIMESTAMPTZ | |


Indexes: `(organization_id, status)`, `external_subscription_id`; UNIQUE on `public_id`.

#### `subscription_items` / `subscription_events`

Items: UUIDv7 PK; `subscription_id` BIGINT FK; `price_id` UUIDv7 FK; `quantity` (seats), `external_item_id`.
Events: UUIDv7 PK; append-only timeline — `subscription_id` BIGINT FK; `event_type`, `payload` before/after, `occurred_at`, `correlation_id`.

#### `invoices` / `invoice_line_items`

**dual-id** on `invoices`: `id` BIGINT IDENTITY PK + `public_id` UUIDv7 UNIQUE.
Invoice statuses: draft, open, paid, void, uncollectible.
Amount fields in cents; `organization_id` / `subscription_id` — BIGINT FK; `external_invoice_id` UNIQUE; `idempotency_key` UNIQUE.
Line items: UUIDv7 PK; `invoice_id` BIGINT FK; quantity DECIMAL(18,6), `amount_cents`, optional `price_id`, `usage_period_id`.

#### `ledger_entries` (immutable journal)


| Column | Type | Notes |
| ------ | ---- | ----- |
| id | BIGINT IDENTITY PK | internal; monotonic for audit |
| public_id | UUIDv7 UNIQUE | ledger list API |
| organization_id | BIGINT FK | → `organizations.id` |
| subscription_id / invoice_id | BIGINT NULL | → dual-id entity |
| entry_type | ENUM | trial_grant, invoice_paid, usage_charge, proration_debit, proration_credit, credit_note, access_revoked_marker, reversal, dunning_fee |
| amount_cents | BIGINT | sign per type / convention |
| currency | CHAR(3) | USD |
| quantity | DECIMAL(18,6) NULL | for metered |
| reverses_entry_id | BIGINT NULL | → `ledger_entries.id` |
| idempotency_key | VARCHAR UNIQUE | |
| correlation_id | VARCHAR | |
| metadata | JSONB | |
| created_at | TIMESTAMPTZ | INSERT only |


DB permissions: application role without UPDATE/DELETE on table (or deny trigger). KPI link: reconciliation accuracy.

#### `usage_events` (monthly partitions)


| Column | Type | Notes |
| ------ | ---- | ----- |
| id | BIGINT IDENTITY PK | internal |
| public_id | UUIDv7 UNIQUE | API / events |
| organization_id / subscription_id | BIGINT FK | dual-id parents |
| feature_key | VARCHAR | |
| quantity | DECIMAL(18,6) | |
| recorded_at | TIMESTAMPTZ | event time |
| idempotency_key | VARCHAR | UNIQUE per org |
| metadata | JSONB | |
| ingested_at | TIMESTAMPTZ | |


**Partitioning (mandatory from Stage 2):** `PARTITION BY RANGE (recorded_at)` — monthly sections `usage_events_YYYY_MM`. Per-partition index: `(organization_id, feature_key, recorded_at)`. `idempotency_key` uniqueness — within partition + check on primary at ingest. Rotation: create partition for month N+1 ahead (Celery/cron); old months — `DETACH PARTITION` → archive/S3, no sharding.

#### `usage_aggregates_hourly` / `usage_periods`

Aggregates: Unique `(organization_id, feature_key, hour_start)`.
Periods: status open|closed|invoiced; linked to close and invoicing.

#### `entitlement_overrides`

UUIDv7 PK; `organization_id` BIGINT FK; manual limits / force allow|deny; `reason` (ticket); `expires_at`; `created_by`.

#### `webhook_events`


| Column | Type | Notes |
| ------ | ---- | ----- |
| id | UUIDv7 PK | |
| provider | VARCHAR | `mock_stripe` |
| provider_event_id | VARCHAR UNIQUE | idempotency key |
| event_type | VARCHAR | `invoice.paid` |
| payload | JSONB | raw body |
| status | ENUM | received, processing, processed, failed, skipped |
| processing_attempts | INT | |
| last_error | TEXT NULL | |
| received_at / processed_at | TIMESTAMPTZ | |


**Webhook idempotency:** `INSERT … ON CONFLICT (provider_event_id) DO NOTHING`; process only new row / `received` status. Retry does not create second ledger entry or second outbox row.

#### `outbox_messages`

BIGINT PK (**no** `public_id`); other fields — as §4.3.3. Partial index: `(published_at NULLS FIRST, created_at) WHERE published_at IS NULL`. Kafka message key = `id`.

#### `reconciliation_runs` / `reconciliation_discrepancies`

Runs: UUIDv7 PK; `run_type` daily|manual; `stats` JSONB; UNIQUE `idempotency_key`.
Discrepancies: UUIDv7 PK; types missing_in_platform, amount_mismatch, status_mismatch, missing_in_stripe, ledger_invoice_mismatch; expected/actual amounts; `details` JSONB.

#### `dunning_campaigns` / `dunning_attempts` (Stage 2+)

Campaigns: UUIDv7 PK; `subscription_id` BIGINT FK; `status` active|paused|completed|exhausted, `grace_until`, `policy_snapshot` JSONB.
Attempts: UUIDv7 PK; `attempt_no`, `scheduled_at`, `executed_at`, `result` succeeded|failed|skipped, `external_charge_id`.

#### `audit_log`

BIGSERIAL; `actor_type` api_key|system|admin; `action`; `resource_*`; before/after JSONB; `correlation_id`; `created_at`. Distinct from ledger: audit — “who did what”; ledger — “what financial entry.”

### 6.4. Idempotency summary


| Operation | Key format | Storage | Scope |
| --------- | ---------- | ------- | ----- |
| Create organization | Header `Idempotency-Key` | unique constraint | permanent |
| Create subscription | Idempotency-Key or `org:{id}:plan:{id}` | `subscriptions.idempotency_key` | permanent |
| Usage event | Client-provided `idempotency_key` (caller-defined; unique per org) | unique per org | permanent |
| Webhook | `provider_event_id` | UNIQUE | permanent |
| Outbox | Domain-specific prefixes (e.g. `webhook:{id}:…`); see ADR-001 | UNIQUE | permanent |
| Invoice | `sub:{id}:period:{end}` | UNIQUE | permanent |
| Ledger entry | `{source}:{source_id}:{entry_type}` | UNIQUE | permanent |
| Reconciliation | `{start}:{end}:daily` | UNIQUE per run key | per run |
| Dunning attempt | `{campaign_id}:{attempt_no}` | UNIQUE | permanent |
| Kafka consumer | key = `outbox_message.id` | offset + business dedupe | — |


On all mutating POST — `Idempotency-Key` header (API contract). **Usage** (`idempotency_key` per org) and **webhooks** (`provider_event_id`) — mandatory domain idempotency (permanent unique). `idempotency_responses` table for replaying stored HTTP response — **deferred** post–Stage 3 ([ADR-014](docs/adr/014-idempotency-responses-defer.md), Defer Accepted); until amendment clients rely on domain keys §6.4.

### 6.5. ER (simplified)

```
organizations ──< subscriptions ──< subscription_items >── prices ──< plans >── products
      │                │
      │                ├──< subscription_events
      │                ├──< dunning_campaigns (Stage 2)
      │                └──< ledger_entries
      ├──< usage_events / usage_aggregates_hourly
      ├──< entitlement_overrides
      └──< invoices ──< invoice_line_items

plans ──< plan_features >── features
```

---

## 7. APIs and events



### 7.1. HTTP API groups overview

Base URL: `https://billing-api.internal/v1`
Auth: `Authorization: Bearer <api_key>`
Tracing: `X-Correlation-ID` (generate if missing).

#### A. Organizations


| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/organizations` | Create (idempotent) |
| GET | `/organizations/{org_id}` | Get |
| PATCH | `/organizations/{org_id}` | Update metadata |
| POST | `/organizations/{org_id}/api-keys` | Issue / rotate key |




#### B. Catalog (Admin)


| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/products` | Create product |
| POST | `/plans` | Draft plan |
| POST | `/plans/{id}/publish` | Publish version |
| POST | `/prices` | Create price |
| POST | `/features` | Define feature |
| PUT | `/plans/{id}/features` | Attach features |
| GET | `/catalog/snapshot` | Full snapshot for cache warming |




#### C. Subscriptions


| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/subscriptions` | Create |
| GET | `/subscriptions/{id}` | Get |
| GET | `/organizations/{org_id}/subscriptions` | List |
| POST | `/subscriptions/{id}/cancel` | Cancel (immediate / end of period) |
| POST | `/subscriptions/{id}/change-plan` | Change plan |




#### D. Entitlements (hot path)


| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/organizations/{org_id}/entitlements` | Full snapshot |
| POST | `/entitlements/evaluate` | Single / batch evaluation |
| POST | `/entitlements/invalidate` | Admin: cache flush |


**Example evaluate request:**

```json
{
  "organization_id": "uuid",
  "checks": [
    { "feature_key": "api_calls", "quantity": 1 },
    { "feature_key": "advanced_analytics" }
  ]
}
```

**Example response:**

```json
{
  "organization_id": "uuid",
  "subscription_status": "active",
  "results": [
    {
      "feature_key": "api_calls",
      "allowed": true,
      "limit": 100000,
      "used": 45231,
      "remaining": 54769,
      "resets_at": "2026-02-28T00:00:00Z"
    }
  ],
  "evaluated_at": "2026-02-09T12:00:00Z",
  "cache_hit": true
}
```



#### E. Usage


| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/usage/events` | Single event |
| POST | `/usage/events/batch` | Up to 1000 |
| GET | `/organizations/{org_id}/usage` | Aggregates |




#### F. Webhooks


| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/webhooks/mock-stripe` | Webhook ingestion (HMAC + idempotency) |
| POST | `/admin/webhooks/{id}/replay` | Replay failed |
| GET | `/admin/webhooks` | Recent list (for demo UI) |




#### G. Reconciliation (Admin)


| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/admin/reconciliation/run` | Manual trigger |
| GET | `/admin/reconciliation/runs` | List |
| GET | `/admin/reconciliation/runs/{id}/discrepancies` | Discrepancies |




#### H. Ledger (Admin / RevOps read)


| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/organizations/{org_id}/ledger` | List entries |
| GET | `/ledger/{entry_id}` | Single entry |




#### I. Dunning (Admin, Stage 2+)


| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/admin/dunning/campaigns` | Active campaigns |
| POST | `/admin/dunning/campaigns/{id}/pause` | Pause |
| POST | `/admin/dunning/campaigns/{id}/resume` | Resume |




#### J. Health and ops


| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/health/live` | Liveness — process alive |
| GET | `/health/ready` | Readiness — DB + Redis + Kafka available |
| GET | `/metrics` | Prometheus (optional) |




### 7.2. Kafka topics


| Topic | Partitions | Retention | Producers | Consumers |
| ----- | ---------- | --------- | --------- | --------- |
| `billing.subscription.events` | 12 | 30d | outbox-relay | warehouse, email, dunning notifier |
| `billing.invoice.events` | 12 | 90d | outbox-relay | finance BI |
| `billing.usage.events` | 24 | 14d | outbox-relay | analytics |
| `billing.entitlement.events` | 6 | 7d | outbox-relay | audit, cache warmers |
| `billing.reconciliation.events` | 3 | 90d | outbox-relay | alerting |
| `billing.ledger.events` | 6 | 90d | outbox-relay | finance audit |
| `billing.dunning.events` | 3 | 30d | outbox-relay | notifier (Stage 2+) |
| `billing.dlq` | 3 | 365d | relay on poison | tooling replay |


**Partition key:** `{organization_id}`.

### 7.3. Envelope and event catalog

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "event_type": "subscription.activated",
  "occurred_at": "2026-02-09T12:00:00Z",
  "producer": "billing-platform",
  "correlation_id": "string",
  "organization_id": "uuid",
  "aggregate_type": "subscription",
  "aggregate_id": "uuid",
  "payload": {}
}
```


| event_type | Topic | Payload summary |
| ---------- | ----- | --------------- |
| `subscription.trial_started` | billing.subscription.events | sub_id, plan_key, trial_end |
| `subscription.activated` | billing.subscription.events | sub_id, plan_key, period |
| `subscription.payment_failed` | billing.subscription.events | attempt_count, next_retry |
| `subscription.past_due` | billing.subscription.events | grace_until |
| `subscription.canceled` | billing.subscription.events | reason, effective_at |
| `subscription.plan_changed` | billing.subscription.events | old/new plan, proration |
| `subscription.access_revoked` | billing.entitlement.events | revoked_features |
| `invoice.created` / `invoice.paid` / `invoice.payment_failed` | billing.invoice.events | amounts, lines, decline codes |
| `usage.period_closed` / `usage.threshold_reached` | billing.usage.events | period, qty, percent |
| `ledger.entry_posted` | billing.ledger.events | entry_id, type, amount_cents |
| `reconciliation.completed` / `reconciliation.mismatch` | billing.reconciliation.events | run_id, summary |
| `dunning.attempt_scheduled` / `dunning.attempt_failed` / `dunning.final_notice` | billing.dunning.events | campaign_id, attempt_no (Stage 2+) |


Pydantic models: `src/billing_platform/events/schemas/v1/*.py`.

### 7.4. Inbound mock Stripe events


| Stripe webhook type | Platform action |
| ------------------- | --------------- |
| `customer.subscription.created` | Insert/update subscription |
| `customer.subscription.updated` | Sync status and period |
| `customer.subscription.deleted` | Mark canceled |
| `invoice.created` | Draft invoice + ledger draft marker (optional) |
| `invoice.finalized` | Update totals |
| `invoice.paid` | Paid + ledger `invoice_paid` + refresh entitlements |
| `invoice.payment_failed` | past_due + start dunning (Stage 2) / event (Stage 1) |
| `charge.dispute.created` | Audit (Stage 2) |


---



## 8. Non-functional requirements



### 8.1. Performance


| Operation | p50 | p99 | Throughput target |
| --------- | --- | --- | ----------------- |
| evaluate (cached) | 5 ms | 50 ms | 3,000 RPS (Stage 3, ≥3 API replicas) |
| evaluate (uncached) | 30 ms | 80 ms | 300 RPS |
| GET entitlements snapshot | 5 ms | 25 ms | 1,000 RPS |
| usage batch (100) | 20 ms | 100 ms | 150 batches/s (= 1,500 events/s) |
| webhook end-to-end | 50 ms | 500 ms | 50/sec |
| outbox lag | — | < 5 s p99 | 1K publish/sec |
| Bearer verify (API key) | — | microseconds | O(1) SHA-256 + unique `key_hash` index; **must not** use a password KDF |


Pools: SQLAlchemy pool_size=20, max_overflow=10 per API instance; Redis ≤ 50 connections.

**Bearer verify:** authenticate is SHA-256 of the Bearer secret then one indexed `SELECT` on `api_keys.key_hash` (ADR-015). Cached evaluate p50/p99 in the table **assume** that path stays in microseconds. A password KDF (bcrypt/argon2/scrypt/PBKDF2) on API-key verify is forbidden.

**Laptop vs stand:** local Compose with **1 API replica** is **capacity characterization**; §8.1.1 profile A DoD requires ≥3 API replicas on a capable stand. **Measured** (2026-03-04 laptop overlay with auth L1 + snapshot L1: 1 replica, 4 Uvicorn workers, pool 8+4, rate limit 0, OTEL off): k6 `POST /v1/entitlements/evaluate` last **hold 1000 RPS** for 22 s (0% fail, 0 dropped; cache-hit **p50 ≈ 6–11 ms**). **Break 1500 RPS**: 0% HTTP fail, `dropped_iterations`, p50 ≈ 204–232 ms, `billing-api` CPU ≈ 4 cores. Grafana `K6_PROFILE=laptop` `ramping-arrival-rate` did **not** `abortOnFail` on `http_req_failed` (fail 0.05%); last progress-line scheduled rate at ramp-end was **1999.92 iters/s** (scheduled arrival, not achieved throughput). See [`docs/perf/2026-03-04-hot-path-perf.md`](docs/perf/2026-03-04-hot-path-perf.md). **Measured** (2026-03-07 `make perf-up` overlay, pool 2+1, relay×2: 1 replica, 4 workers, rate limit 0, OTEL off): last **hold 1500 RPS** (22 s, 0% fail, 0 dropped, p50 ≈ 13 ms); **break 2000 RPS** (`dropped_iterations` 2822, p50 ≈ 89 ms); limiter **SUT evaluate-path latency** (CPU peg unproven on WSL `docker stats`) — [`docs/perf/2026-03-07-prodlike-hunt.md`](docs/perf/2026-03-07-prodlike-hunt.md). Stage 3 table throughput (**3,000** cached evaluate RPS) is the **scaled target** (≥3 API replicas × the 1000 RPS / 4-worker hold). Per-process auth L1 (ADR-015) and snapshot L1 (ADR-003). Pre-L1 SHA-256 overlay on the same knobs: last hold 400 RPS, break 500 RPS.

**DB routing (Stages 2–3):** separate DSN `DATABASE_URL` (primary, RW) and `DATABASE_READ_URL` (replica, RO). Writes for subscriptions / outbox / webhooks / ledger / usage ingest — primary only. Evaluate on cache miss and Admin usage reports — replica in Stage 3 if `replica_lag_seconds` below threshold; else fallback primary. Sharding not in NFR Stages 1–3 (see §12.13).

#### 8.1.1. Load test profiles (end of Stage 3)

Goal — **validate NFR §1.4 / table above**, not “hundreds of thousands RPS for a record.” Stage 3 peak: **~3,000** evaluate/s + **~1,500** usage events/s + light admin read **500**/s ≈ **~5,000** HTTP RPS on hot paths (band **4,500–6,000**). **100k+ RPS** is **not** in DoD: above declared scale and would require revisiting §1.4 and “no sharding” (§12.13).

| Profile | Purpose | Target intensity | Duration | Success criterion |
| ------- | ------- | ---------------- | -------- | ----------------- |
| **A — Evaluate peak** | main hot path | **3,000** RPS `POST /entitlements/evaluate` (Stage 3 NFR; cached-heavy) | **10 min** | error rate < 0.1%; p99 < **50 ms** with ≥3 API replicas |
| **B — Usage ingest** | write path | **1,500** events/s (batch ≤1000; Stage 3 NFR equivalent) | **10 min** | 2xx idempotency; no 5xx growth; PG without queue storm |
| **C — Mixed** | prod-like | **5,000** HTTP RPS mix: evaluate **3,000** + usage **1,500** + admin read **500** (band **4,500–6,000**) | **10 min** | profiles A/B SLA; `outbox_lag_seconds` p99 < 30 s under peak (above steady SLO) |
| **D — Soak (recommended)** | leaks / degradation | **0.3×** peak C: evaluate **900** + usage **450** + admin **150** | **30–60 min** | stable p99/heap; no unbounded unpublished outbox growth |
| **E — Ceiling (optional)** | find ceiling | **ramping-arrival-rate** until k6 `abortOnFail` (Grafana breakpoint). **8,000** RPS (`K6_CEILING_RPS` default) is a search upper bound, **not** a constant hold | until abort | record breaking point in report; **not** DoD |

Tool: **k6** (preferred) or Locust; scenarios in `scripts/perf/` or `tests/load/`; report — `docs/perf/YYYY-MM-DD-stage3-load.md` (commands, RPS, p50/p99, error rate, bottleneck notes).

**Forbidden in DoD:** claiming “passed load” at Stage 2 numbers (1k evaluate/s) as substitute for Stage 3 profile A; running 100k+ RPS on laptop without dedicated stand and calling it prod validation.

### 8.2. Idempotency and consistency

- webhooks: `INSERT … ON CONFLICT DO NOTHING`; process only new / `received`;
- usage: duplicate → 200 with original id;
- ledger: unique `idempotency_key`; retries do not create second entry;
- outbox → Kafka: at-least-once; consumers dedupe by `event_id`;
- subscription state machine rejects illegal transitions;
- one webhook = one DB transaction including ledger + outbox.



### 8.3. Multi-tenant isolation

- every request filtered by `organization_id` from auth context;
- API keys scoped (except platform_admin);
- PostgreSQL RLS — optional hardening Stage 3;
- Kafka events always carry `organization_id`;
- cache keys only with prefix `ent:org:{org_id}:`;
- mandatory negative integration tests for cross-organization access.



### 8.4. Security (secrets and protection)


| Area | Requirement |
| ---- | ----------- |
| Transport | TLS 1.3 (ingress) |
| Secrets | K8s Secrets / Vault / env on Compose; **not in git**; `.env.example` without values |
| Webhook secrets | separate `MOCK_STRIPE_WEBHOOK_SECRET`; rotation with two-secret overlap (Stage 3) |
| API keys | SHA-256 hex unique lookup (ADR-015); logs only prefix; no password KDF on verify |
| Webhooks | HMAC, ±5 min tolerance, constant-time compare |
| PII | access restriction; email encryption — Stage 3 |
| Rate limiting | Redis token bucket: 1000 req/min per key default; 429 + `Retry-After` |
| Audit | all admin mutations → `audit_log` |
| Dependencies | `uv lock` + Dependabot |
| Least privilege | separate DB roles: api (DML), migrator (DDL), ledger without UPDATE/DELETE |




### 8.5. Observability: SLI / SLO, alerts, runbooks



#### SLI and SLO (internal targets)


| SLI | SLO (Stage 2+) | KPI link |
| --- | -------------- | -------- |
| Share of successful `entitlement.evaluate` (non-5xx) | ≥ 99.9% over 30 days | limit tickets |
| p99 `entitlement.evaluate` (cached) | < 50 ms | product UX |
| Share of webhooks in `processed` within 60 s of receive | ≥ 99.9% | MRR leakage / false blocks |
| `outbox_lag_seconds` p99 | < 5 s | warehouse / dunning freshness |
| Reconciliation accuracy (invoices without discrepancy) | ≥ 99.5% per month | Finance trust |
| Webhook loss after persist | 0% | billing reliability |




#### Metrics and alerts

**Logs (structlog):** timestamp, level, event, correlation_id, organization_id, duration_ms.

**OpenTelemetry spans:** `http.request`, `db.query`, `redis.command`, `kafka.produce`, `entitlement.evaluate`, `webhook.process`, `outbox.relay.batch`, `reconciliation.run`, `dunning.attempt`.

**Metrics:** `entitlement_evaluate_total`, `entitlement_cache_hit_ratio`, `webhook_processing_duration_seconds`, `outbox_unpublished_count`, `outbox_lag_seconds`, `reconciliation_discrepancy_amount_cents`, `usage_events_ingested_total`, `ledger_entries_posted_total`, `dunning_campaigns_active`, `http_rate_limited_total`.


| Alert | Condition | Priority | Runbook |
| ----- | --------- | -------- | ------- |
| OutboxLagHigh | `outbox_lag_seconds` > 300 | P2 | `docs/runbooks/outbox-lag.md` |
| WebhookFailRate | failed_rate > 1% over 15 min | P2 | `docs/runbooks/webhook-replay.md` |
| ReconMismatch | discrepancy amount > $100 | P3 | `docs/runbooks/reconciliation-mismatch.md` |
| EntitlementLatency | p99 > 100 ms for 5 min | P3 | check Redis / DB pool |
| ReadyProbeFail | ready fails > 2 min | P1 | availability incident |
| DunningStuck | attempt overdue > 1 h (Stage 2) | P3 | `docs/runbooks/dunning-stuck.md` |


**Runbook template:** symptoms → check metrics/logs → safe actions (webhook replay, dunning pause, scale relay) → escalation → postmortem for P1/P2.

#### 8.5.1. Prometheus / Grafana (Stage 3 — scoped Adopt)

Stages 1–2 sufficient: structlog + OpenTelemetry (Console/OTLP) + documented SLI/alerts. **Prometheus + Grafana not mandatory** for Stage 2 DoD.

**ADR-013 (Accepted, amended 2026-03-02): scoped Adopt** — opt-in Compose profile `observability` ships a local/demo **LGTP stack** (Grafana Alloy + Tempo + Loki + Prometheus + Grafana). Default `make compose-up` has **no** observability backends and `OTEL_SDK_DISABLED=true`.

1. **Default compose:** structlog + OTel stubs; tracing off under load (`make load-*` forces `OTEL_SDK_DISABLED=true`).
2. **Profile `observability`:** OTLP → Alloy → Tempo/Loki/Prometheus; Grafana :3000; tail-based sampling and retention per [`deploy/observability/README.md`](deploy/observability/README.md).
3. **On Adopt (scoped):** minimal alerts OutboxLagHigh / EntitlementLatency + SLO dashboard; no exporter zoo; no `GET /metrics` on API (OTLP export via Alloy).
4. **Deferred:** Mimir, production object storage, full multi-tenant SRE platform; metric stub hot-path wiring remains partial (see [`docs/slo.md`](docs/slo.md)).

Kafbat UI **does not** replace application metrics: it is for debugging topics/consumer lag on the bus.

### 8.6. Health, readiness, graceful shutdown


| Probe | Path | Success condition |
| ----- | ---- | ----------------- |
| Liveness | `GET /health/live` | process responds (no dependencies) |
| Readiness | `GET /health/ready` | PostgreSQL ping + Redis ping + Kafka metadata (or circuit with explicit degraded mode) |


**Graceful shutdown:**

- API: on SIGTERM stop accepting new connections; wait for in-flight (30 s timeout); close pools;
- relay: no new batch after SIGTERM; wait for current batch publish;
- worker: Celery warm shutdown;
- webhooks: persist-first ensures event not lost on kill mid-process.



### 8.7. Availability and disaster recovery


| Stage | Availability target |
| ----- | ------------------- |
| 1 | 99.5% (best effort) |
| 2 | 99.9% (multi-instance) |
| 3 | 99.95% |


RPO: 0 for financial data (synchronous commit on primary). RTO: 4 h (Stage 1) / 1 h (Stage 3). Backups: daily full + WAL. Read replica does not replace backups and does not accept writes on primary failure without separate failover runbook.

### 8.8. Production-ready checklist

- [x] Idempotency for webhooks, usage, ledger, subscriptions *(usage ingest — Stage 2; webhooks/ledger/subscriptions — Stage 1)*
- [x] Transactional outbox + DLQ + lag alert *(runbook `outbox-lag`; metric/alert documented in `docs/slo.md`)*
- [x] First-class reconciliation + runbook
- [x] Secrets outside git; webhook HMAC
- [x] Rate limiting on API keys *(Stage 2, §11.2)*
- [x] `/health/live` + `/health/ready`
- [x] Graceful shutdown for all processes *(API lifespan + Uvicorn timeout; documented in README)*
- [x] SLI/SLO documented; alerts with runbooks *(docs/slo.md + runbooks; alert wiring — ops)*
- [x] Structured logs + OTel spans *(HTTP spans; Console exporter locally)*
- [x] Migrations with zero-downtime plan (§8.9) *(ADR-009; Stage 1 — expand-only)*
- [x] CI as mandatory gate (lint, types, tests) *(Makefile + `.github/workflows/ci.yml` stub)*
- [x] Thin admin UI for demo value



### 8.9. Zero-downtime schema migrations

**Principle:** expand → migrate → contract. Never combine breaking DDL with deploy requiring old schema.


| Step | Action | Example |
| ---- | ------ | ------- |
| 1. Expand | Add nullable column / new table | `ledger_entries` alongside old fields |
| 2. Dual-write | Code writes old and new (if needed) | one release |
| 3. Backfill | Background job fills history | batched UPDATE/INSERT |
| 4. Switch-read | Read from new source | feature flag |
| 5. Contract | Drop old field in separate release | after stabilization |


**Forbidden in hot window without offline:** `ALTER TYPE` enum with large table rewrite; long `CREATE INDEX` without `CONCURRENTLY`; blocking `ALTER COLUMN TYPE`.

**Migration release checklist:** `alembic upgrade` on prod-volume copy within target window; rollback script; readiness probe stable during expand.

---



## 9. Repository structure

```
saas-billing-entitlements/
├── .github/workflows/          # ci.yml, deploy-staging.yml, dependabot.yml
├── alembic/
├── deploy/
│   ├── docker/                 # Dockerfile.api|worker|outbox-relay|mock-stripe|demo-ui
│   ├── compose/                # docker-compose.yml, .test.yml, init-kafka-topics.sh
│   │                           # (+ kafbat-ui from Stage 2; opt. profile observability → deploy/observability/)
│   └── helm/billing-platform/  # Deployments api/worker/relay, HPA stub, probes
├── docs/
│   ├── adr/                    # 001-outbox, 002-kafka, 003-entitlement-cache, 006-ledger
│   ├── runbooks/               # outbox-lag, webhook-replay, reconciliation-mismatch, dunning-stuck
│   ├── perf/                   # load test reports (§10.5)
│   ├── slo.md                  # SLI/SLO and alerts
│   └── spec.md                 # (repo root: spec.md; tree — orientation)
├── scripts/                    # seed_catalog, generate_test_webhook, reconcile_manual
│                               # (+ perf/: k6/locust scenarios Stage 3)
├── src/billing_platform/
│   ├── main.py, config.py, logging.py, telemetry.py
│   ├── api/v1/                 # HTTP routes + colocated Pydantic DTOs (organizations, catalog,
│   │                           # subscriptions, entitlements, usage, webhooks, admin/*, ledger, dunning)
│   ├── domain/models/          # ORM + state_machines/subscription.py
│   ├── services/               # evaluator, webhook_processor, reconciliation, ledger, dunning, outbox…
│   ├── integrations/           # payment_provider port + mock_stripe; kafka; redis
│   ├── events/schemas/v1/      # Kafka envelope / event payloads (not HTTP DTOs)
│   ├── workers/tasks/          # usage_aggregation, period_close, grace, reconciliation, dunning
│   └── outbox_relay/
├── demo_ui/                    # thin frontend (§13)
├── tests/                      # unit / integration / e2e + factories
├── pyproject.toml
├── uv.lock
├── alembic.ini
├── Makefile
└── README.md
```

---



## 10. Testing strategy and CI/CD



### 10.1. Pyramid


| Level | Effort share | Scope | Tools |
| ----- | ------------ | ----- | ----- |
| Unit | 60% | evaluator, state machine, proration, HMAC, outbox payloads, ledger rules | pytest, mocks |
| Integration | 30% | API + PG/Redis/Kafka (testcontainers) | pytest-asyncio, httpx |
| E2E | 10% | trial→paid→usage→invoice→recon; failed→grace→dunning→revoke | compose (roadmap) |
| Contract | ongoing | event schema snapshots; OpenAPI compat | schemathesis (Stage 2) |




### 10.2. Mandatory cases (minimum)

**Unit:** illegal `canceled`→`trialing` transition; past_due+grace → degraded; quota exhausted → deny; duplicate webhook without second outbox row / ledger; amount mismatch in reconciliation; ledger reversal does not delete original row.

**Integration:** subscription + `invoice.paid` → active + cache + ledger; duplicate usage → 200; relay → Kafka envelope; reconciliation discrepancies; cross-org access → 403; rate limit → 429; readiness fails without DB.

**E2E:** full billing cycle; payment failure scenario + (Stage 2) dunning step; demo UI happy path. `tests/e2e/` **not yet created** (roadmap); critical paths covered by **integration** (Testcontainers) + demo-ui walkthrough (§13.7). Full Playwright/compose e2e suite — post–Stage 3 without blocking current DoD.

### 10.3. CI/CD (GitHub Actions)

Every PR: `ruff check/format`, `mypy src`, `pytest unit` (coverage ≥ 80%), `pytest integration` via Testcontainers on the Docker host (`make test-integration`; Helm installed in CI), `docker build` API. Live Locust smoke is a **separate** CI job (`load-locust-smoke`), not mixed into unit/integration.

Branch protection: lint, typecheck, unit, integration, 1 review.

Merge to main: push images → `helm upgrade` staging → smoke e2e.

CI secrets — GitHub Secrets only; images without embedded keys.

### 10.4. Quality gates


| Gate | Threshold |
| ---- | --------- |
| Unit coverage | ≥ 80% (services + domain) |
| mypy | strict, 0 errors |
| Ruff | 0 violations |
| Integration | 100% pass |
| OpenAPI | no breaking without version bump |
| Migration | `upgrade head` on empty DB < 60 s; expand/contract documented for breaking |


### 10.5. Load testing (end of Stage 3 / project closure)

Runs **after** functional Stage 3 DoD (Helm, replica, HA tooling), on stand close to target topology (multiple API replicas, Redis, PG primary [+ replica], Kafka, worker, relay). Local laptop Compose (typically **1 API replica**) is **capacity characterization / smoke**, not profile A DoD. Profile A acceptance remains **3,000** evaluate RPS / 10 min / p99 < 50 ms with ≥3 API replicas on a capable stand (§8.1.1).

**Mandatory:**
- scenarios for profiles **A** and **C** (§8.1.1) with report artifact;
- record stand configuration (replica count, pool sizes, CPU/RAM limits);
- verify invariants under load: usage/webhook idempotency, no dual-write, outbox lag within profile C limits.

**Recommended:** profile **D** (soak). **Optional:** profile **E** (ceiling) — capacity ceiling exploration, not merge blocker.

**Do not mix** with unit/integration CI on every PR: load suite — separate job/Makefile target (`make load-test` or equivalent), manual / tag on stand.

---

## 11. Acceptance criteria

Production-grade acceptance bar: measurable SLOs, idempotency, and financial invariants — not UI-only smoke.

### 11.1. Stage 1

- [x] `docker compose up` brings everything up; README happy path < 15 minutes
- [x] org + subscription → webhook → status `active`
- [x] `POST /entitlements/evaluate` reflects published plan
- [x] second evaluate: `cache_hit=true`; after webhook invalidation < 60 s
- [x] duplicate webhook does not duplicate outbox rows or ledger entries
- [x] relay publishes ≥ 5 event types to Kafka
- [x] minimal ledger entries for payment / activation
- [x] manual reconciliation creates run + discrepancy on seeded mismatch
- [x] Alembic up/down for domain tables; secrets not in repository
- [x] `/health/live`, `/health/ready`; graceful shutdown in README
- [x] structlog with `correlation_id`, `organization_id`
- [x] OpenTelemetry trace visible locally (Tempo via profile `observability`, or Console when OTEL enabled without OTLP) *(Stage 1: ConsoleSpanExporter; OTLP — optional)*
- [x] pytest ≥ 80%, integration green in CI; mypy + ruff clean *(local CI: `make lint typecheck test-unit`; cov ≥ 80%)*
- [x] OpenAPI `/docs` current; ADR for outbox, Kafka, ledger written
- [x] demo UI shows organization, subscription, entitlements, webhook status



### 11.2. Stage 2 (Stage2 Done)

- [x] batch 1000 usage; hourly aggregates correct
- [x] period close → line items → ledger usage_charge → sync mock Stripe
- [x] reconciliation cron finds seeded discrepancy; mismatch event in Kafka
- [x] reconciliation includes ledger ↔ invoice
- [x] grace: access not revoked until `grace_period_days`; after — revoke
- [x] dunning: campaign on payment_failed; scheduled attempts; pause/resume
- [x] immediate upgrade updates entitlements
- [x] Celery tasks idempotent on **retry**
- [x] rate limiting returns 429 under load test
- [x] SLI/SLO in `docs/slo.md`; alerts + runbooks: outbox-lag, webhook-replay, reconciliation-mismatch, dunning-stuck
- [x] zero-downtime migration plan applied to at least one “hot” table
- [x] UI: usage, reconciliation runs, dunning card
- [x] Kafbat UI in Compose: cluster UI opens; billing topics and consumer groups visible (at least outbox-relay)



### 11.3. Stage 3 (Stage3 Done, load A/C PARTIAL on laptop)

- [x] Helm in kind/minikube: api + worker + relay with probes
- [x] HPA stub; rate limiting 429; API key rotation without downtime
- [x] 2 relay replicas without double publish (`idempotency_key`)
- [x] load profiles **A** and **C** (§8.1.1 / §10.5): report in `docs/perf/`; evaluate peak **3,000** RPS / 10 min, p99 < 50 ms with ≥3 API replicas; mixed **5,000** RPS (band **4,500–6,000**) / 10 min *(laptop Compose = capacity characterization / smoke, not profile A DoD; full RPS on capable stand ≥3 API — PARTIAL)*
- [x] ADR Prometheus/Grafana: **scoped Adopt** Accepted (§8.5.1, ADR-013 amended 2026-03-02); LGTP via opt-in profile `observability`; SLI via OTel stubs + `docs/slo.md` + runbooks; default compose without Prom/Grafana
- [x] read replica connected: evaluate/usage reports use RO DSN; writes — primary only
- [x] `usage_events` — RANGE by month; next-month partition creation automated
- [x] ADR “no sharding in Stage 1” (§12.13) in `docs/adr/`
- [x] DLQ replay script; webhook rejected on invalid signature
- [x] webhook secret rotation with overlap



### 11.4. DoD per product feature

1. Code + tests + types + API documentation
2. Migration up/down (and expand/contract if breaking)
3. Log fields + span + metric
4. Idempotency documented and tested
5. PR review + CI green
6. No secrets in repository
7. If money impact — ledger entry and/or reconciliation checkpoint

---



## 12. Architecture decisions

ADR-style block: context → decision → alternatives → trade-offs. Extends §4.

### 12.1. Why PostgreSQL as source of truth, not Kafka?

**Context.** Strict subscription invariants, unique idempotency keys, financial amounts, ledger, and “webhook + outbox” transactions needed.

**Decision.** OLTP in PostgreSQL; Kafka — integration fact bus after commit only.

**Alternatives.** Event sourcing as primary — excessive for Stages 1–2 and complicates provider reconciliation. CQRS with separate read model for entitlements — possible Stage 3 optimization, not day-one.

**Trade-off.** Small outbox→Kafka delay acceptable for analytics; unacceptable for hot entitlement path, hence synchronous evaluate.

### 12.2. Why transactional outbox, not “publish after commit”?

**Context.** Dual write between DB and broker — classic source of “lost” and “false” billing events.

**Decision.** One transaction: domain + ledger + outbox; relay with `SKIP LOCKED`; message key = `outbox_messages.id`.

**Alternatives.** Debezium CDC — powerful but heavier ops and raw row changes vs versioned domain events. Consumer inbox without producer outbox — does not fix lost publish.

**Trade-off.** At-least-once instead of exactly-once; consumers must dedupe.

### 12.3. Why separate outbox relay process, not Celery task?

**Context.** Kafka publish — critical reliability path; predictable poll, lag metrics, HA replicas needed.

**Decision.** Separate `outbox-relay` process; Celery — aggregates, period close, grace, reconciliation, dunning.

**Alternatives.** Celery beat “publish every N seconds” — simpler start, worse failure isolation and backpressure. LISTEN/NOTIFY — insufficient scale and replay.

**Trade-off.** Another deployable unit in Compose/Helm.

### 12.4. Why entitlement evaluation does not read Kafka?

**Context.** Product API needs millisecond response and consistency with current subscription.

**Decision.** Read path: Redis → PostgreSQL. Kafka informs downstream (banners, email, warehouses, dunning notifier), not authorize source.

**Alternatives.** Materialized entitlement service via consumer — eventual consistency, harder “why denied” debugging.

**Trade-off.** Cache may lag TTL; mitigated by version bump and short TTL.

### 12.5. Why mock Stripe from day one, not live Stripe?

**Context.** Local reproducibility without live payment-provider credentials; PCI scope and billing account not required for development and CI.

**Decision.** `PaymentProviderPort` + `mock-stripe` with compatible webhook signatures; domain does not import Stripe SDK; idempotency on `provider_event_id` from day one.

**Alternatives.** Hand-recorded webhooks without provider — does not show reconciliation and signature verify. Live Stripe immediately — slow feedback and secrets in demo.

**Trade-off.** Mock simplifies real Stripe edge cases; port contract must cover them in tests on future swap.

### 12.6. Why Redis for entitlement cache, not PostgreSQL only?

**Context.** Hot path: thousands of evaluate/sec in Stage 3; repeated identical checks in product gateway.

**Decision.** Entitlement snapshot in Redis, TTL 30–60 s, versioned key; on miss — assemble from DB.

**Alternatives.** DB + PgBouncer only — simpler, higher latency/load. CDN/edge cache — risky for security-sensitive authorize without careful invalidation.

**Trade-off.** Brief staleness; product policy must allow (or shorter TTL on critical features).

### 12.7. Why usage written separately from evaluate?

**Context.** Mixing “check entitlement” and “debit quota” in one call complicates idempotency and client retries.

**Decision.** Evaluate — read-only; `POST /usage/events` — write with Idempotency-Key; quotas read aggregates.

**Alternatives.** Atomic check-and-increment in Redis — faster, harder reconciliation with invoice and recovery after failure.

**Trade-off.** Small over-use between evaluate and usage write possible; for hard quota Stage 3 optional Lua check-and-incr enforcement.

### 12.8. Why daily reconciliation and ledger, not “trust webhooks only”?

**Context.** Webhooks lost, duplicated, out-of-order; finance needs provable convergence; KPI reconciliation accuracy ≥ 99.5%.

**Decision.** Append-only ledger + reconciliation cron compares registries; discrepancies first-class; large amounts alert; corrections — reversal only.

**Alternatives.** Manual SQL month-end — slow, not scalable. Flink streaming join — excessive Stages 1–2.

**Trade-off.** Reconciliation detects but does not always auto-fix; remediation — runbook + replay + compensating entry.

### 12.9. Why dunning from Stage 2, not MVP?

**Context.** Stage 1 must prove webhook persist, outbox, entitlements, reconciliation skeleton; full dunning without reliable domain bloats MVP.

**Decision.** Stage 1 — payment_failed/past_due events; Stage 2 — campaigns, attempts, pause, notifier events. KPI: involuntary churn.

**Alternatives.** Full ESP+templates immediately — outside Platform scope.

**Trade-off.** Stage 1 partial churn reduction via grace and correct revoke; full cycle — Stage 2.

### 12.10. Why API keys in Stage 1, not OAuth2 immediately?

**Context.** Fast MVP for internal services; fewer IdP moving parts.

**Decision.** High-entropy API keys (`bp_` + CSPRNG) stored as SHA-256 hex with unique `key_hash` lookup (not a password KDF; ADR-015); roles on the key; Stage 2 — OAuth2 client credentials.

**Alternatives.** External IdP JWT day one — better for enterprise, slows local demo.

**Trade-off.** Rotation and revoke required (Stage 3 DoD).

### 12.11. Why thin admin UI, not OpenAPI only?

**Context.** RevOps and CS need subscription status, entitlement cache state, and reconciliation mismatches visible without assembling curl calls for every step.

**Decision.** Demo UI on **Vite + React + TypeScript** calls same Admin/Internal API; no billing logic on client (see §13). HTML templates without TypeScript — not primary.

**Alternatives.** Swagger + Grafana only — technically enough, weaker RevOps/CS narrative.

**Trade-off.** Another package in repo; UI intentionally thin.

### 12.12. Trade-off summary


| Decision | Gain | Cost |
| -------- | ---- | ---- |
| Outbox + Kafka | Reliable integration contract | Ops + at-least-once |
| Redis entitlement cache | Low latency | Brief staleness |
| Mock Stripe + Port | Demo and tests without PCI | Not all live Stripe edges |
| Append-only ledger | Provable reconciliation / audit | More rows, reversal discipline |
| Dunning from Stage 2 | Lower involuntary churn | Orchestration complexity |
| Read replica + RANGE partitions (Stage 3) | Read/usage scale without shards | Eventual consistency on RO; replica ops |
| Application-level tenant filter | Stage 1 simplicity | RLS deferred |
| Celery for batch/cron | Familiar Python tooling | Not primary event bus |
| Short cache TTL | Simpler invalidation | Slightly higher DB load |




### 12.13. Why no PostgreSQL sharding Stages 1–3? (ADR)

**Context.** System design often proposes immediate billing shard by `organization_id`. That breaks “webhook + outbox + ledger” transactions, complicates reconciliation and unique keys.

**Decision.** Stages 1–3: one write primary; Stage 3 — **read replica** for evaluate/reports and **RANGE partitions** on `usage_events` by month. Full shard cluster — roadmap only.

**Sharding transition criteria (all at once, not earlier):**

1. primary sustainably saturated on **writes** (WAL/IOPS/CPU), not SELECT only;
2. partitions + replica + PgBouncer + entitlement cache deployed and measured;
3. replica lag grows from write volume, not heavy reports;
4. explicit cross-shard reconciliation and webhook idempotency plan.

**Alternatives.** Citus / manual org shard — powerful but premature at current scale and breaks simple TX. Vertical scale only — cheaper until threshold, does not manage `usage_events` growth without partitions.

**Trade-off.** Until shards, evaluate on replica may be slightly stale; for hard authorize on critical features — read primary or short cache TTL.

### 12.14. Identifier policy: UUIDv7, dual-id, composite keys, outbox BIGINT (ADR)

**Context.** Unified PK/FK policy for billing: hot tables written often, API must not expose sequential id, catalog and operational journals should not carry unnecessary dual-id.

**Decision.**

1. **UUIDv7 vs UUIDv4.** Default surrogate and `public_id` — **UUIDv7** (time ordering → fewer B-tree page splits). UUIDv4 (`gen_random_uuid()`) as PK on frequently written tables forbidden without separate ADR; v4 OK only for opaque secrets/tokens, not entity PK.
2. **Dual-id only on** `organizations`, `subscriptions`, `invoices`, `usage_events`, public `ledger_entries`: internal `BIGINT IDENTITY` + external `public_id` UUIDv7; FK in DB — BIGINT. Catalog, webhooks, reconciliation, dunning — single-column UUIDv7 PK: write volume and public contract do not justify second index.
3. `plan_features` **not composite PK.** Has `limit_value`, `is_enabled`, `enforcement_mode` — own row lifecycle; surrogate UUIDv7 + `UNIQUE (plan_id, feature_id)`. Composite PK only for pure M:N without attributes.
4. `outbox_messages` **— BIGINT PK without** `public_id`**.** Append-only, not client-exposed; monotonic `id` = Kafka key and simple aggregate references; dual-id here — extra index without API benefit.

**Alternatives.** UUIDv4 everywhere — simpler start, worse index locality. Dual-id on all tables — bloats ORM/logs/events. `(organization_id, id)` as PK “for multi-tenancy” — composite FK everywhere and breaks simple `aggregate_id` in outbox.

**Trade-off.** API/OpenAPI paths use `public_id`; services map public→internal at boundary. BIGINT leak risk from wrong serialization removed by rule: sequential `id` never in DTO.

---



## 13. Demo UI (thin frontend)



### 13.1. Is UI needed?

**Yes — thin admin UI.** Subscription status, entitlement snapshot, usage, ledger, and reconciliation runs are easier to operate than OpenAPI alone. **All business logic stays on the backend**; UI is a client to Admin/Internal API.

### 13.2. UI goal

- run §13.7 walkthrough in 5–10 minutes without manual curl assembly;
- show RevOps/CS personas: “I see subscription, entitlements, mismatch, dunning step”;
- demonstrate the Platform as operable (replay, webhook statuses, health), not just tables.



### 13.3. Screens (6–8)


| # | Screen | Shows | APIs |
| - | ------ | ----- | ---- |
| 1 | Organization list | tenants, billing email | GET organizations |
| 2 | Organization card | active subscription, status, period | GET org + subscriptions |
| 3 | Entitlement snapshot | features, limits, used/remaining, cache_hit | GET entitlements / evaluate |
| 4 | Usage | aggregates by feature_key / period | GET usage |
| 5 | Webhooks | recent events, status, replay | GET/POST webhooks admin |
| 6 | Reconciliation | runs, counts, discrepancy list | reconciliation admin |
| 7 | Ledger | recent organization entries | GET ledger |
| 8 | Dunning (Stage 2) | campaign, attempts, pause | dunning admin |
| 9 | Catalog (optional) | plans/prices read-only or publish | catalog admin |


Simple navigation: org → subscription → entitlements → usage → recon/ledger — task-focused screens only.

### 13.4. What UI deliberately does NOT do

- does not compute proration, grace, entitlement policy, dunning schedule;
- does not write directly to PostgreSQL / Redis / Kafka;
- does not store provider secrets;
- is not a customer portal (card payment, payment method change);
- does not duplicate RBAC — relies on API key / Admin API session;
- does not mutate ledger (view only; corrections via backend admin API).



### 13.5. Recommended UI stack

Lightweight SPA on **Vite + React + TypeScript**. Calls only backend OpenAPI; client generation from OpenAPI desirable; minimal CSS without heavy design system. **Do not** use HTML/Jinja templates as primary (acceptable only as Stage 0 temporary scaffold). Next.js / full admin console — out of scope. Delivery: separate `demo-ui` container in Compose; may not deploy to production.

### 13.6. How UI proves backend value


| Demo moment | What backend proves |
| ----------- | ------------------- |
| Second evaluate with cache_hit | Redis cache strategy |
| Webhook replay | idempotency + persist-first |
| Discrepancy after cron | reconciliation as first-class |
| Ledger entry appears | auditable charge accounting |
| Subscription status on screen | state machine + webhook handler |
| Kafka facts / timeline | outbox relay delivered events |
| Pause dunning (Stage 2) | payment recovery orchestration |


If UI is temporarily unavailable, operations still work via OpenAPI + OTel/metrics tooling + Kafka console. UI remains a Stage 1 deliverable for local and staging validation.

### 13.7. Local demo walkthrough

1. `docker compose up` — api, worker, relay, postgres, redis, kafka, mock-stripe, demo-ui.
2. In UI/API create organization and trial subscription.
3. Show evaluate: plan features, `cache_hit` on second request.
4. Simulate `invoice.paid` → status active, ledger entry, Kafka event.
5. Record usage batch → aggregates.
6. Simulate `payment_failed` → past_due / grace; show entitlement not yet revoked; (Stage 2) dunning step.
7. Run reconciliation with seeded mismatch → discrepancies screen.
8. Replay same webhook → no duplicate outbox / ledger.
9. (Optional) stop relay for a minute → growing lag → start → catch up; show `/health/ready`.

---



## Appendices



### A. Subscription state machine

```
                    ┌─────────────┐
                    │ incomplete  │
                    └──────┬──────┘
                           │ payment OK
                           ▼
┌──────────┐         ┌─────────────┐         ┌──────────┐
│ trialing │────────▶│   active    │────────▶│ canceled │
└──────────┘         └──────┬──────┘         └──────────┘
                            │ payment failed
                            ▼
                     ┌─────────────┐
                     │  past_due   │  ← dunning (Stage 2+)
                     └──────┬──────┘
                            │ grace expired / dunning exhausted
                            ▼
                     ┌─────────────┐
                     │   unpaid    │
                     └─────────────┘
```

Illegal transitions (e.g. `canceled` → `trialing`) are rejected; return only via new subscription.

### B. Environment variables (reference)


| Variable | Default / example | Purpose |
| -------- | ----------------- | ------- |
| `DATABASE_URL` | postgresql+asyncpg://… | PostgreSQL |
| `REDIS_URL` | redis://redis:6379/0 | Cache and rate limit |
| `KAFKA_BOOTSTRAP_SERVERS` | kafka:9092 | Broker |
| `ENTITLEMENT_CACHE_TTL_SECONDS` | 60 | Entitlement snapshot TTL |
| `OUTBOX_BATCH_SIZE` | 100 | Relay batch size |
| `OUTBOX_MAX_ATTEMPTS` | 10 | DLQ threshold |
| `WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` | 300 | HMAC tolerance |
| `MOCK_STRIPE_WEBHOOK_SECRET` | (from Secrets) | Webhook signature |
| `API_RATE_LIMIT_PER_MINUTE` | 1000 | Token bucket |
| `RECONCILIATION_ALERT_AMOUNT_CENTS` | 10000 | Alert threshold ($100) |
| `GRACE_ENFORCEMENT_INTERVAL_SECONDS` | 60 | Grace check period |
| `DUNNING_ENABLED` | false (Stage 1) / true (Stage 2) | Orchestration flag |
| `MOCK_STRIPE_BASE_URL` | [http://mock-stripe:8001](http://mock-stripe:8001) | Provider client |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | [http://otel-collector:4317](http://otel-collector:4317) | Traces |
| `SHUTDOWN_GRACE_SECONDS` | 30 | Graceful shutdown |




### C. Local demo checklist

1. `uv sync` && copy `.env.example` → `.env` (deterministic demo key/org already set)
2. `make compose-core` (or `compose-all`) — `billing-api` runs `alembic upgrade head` + deterministic demo seed on start (`RUN_MIGRATIONS` / `RUN_DEMO_SEED`; optional manual: `python -m billing_platform.bootstrap` / `scripts/seed_catalog.py`)
3. Open demo-ui (`http://localhost:8080`) / OpenAPI `/docs`
4. Run walkthrough §13.7
5. Check outbox_lag metrics, `/health/ready`, and `entitlement.evaluate` trace
6. Optional additive multi-tenant data: `scripts/seed_prod_like.py` (does not replace demo seed)



### D. ADR index (for implementation)


| ADR | Topic |
| --- | ----- |
| 001 | Transactional outbox |
| 002 | Kafka as integration bus |
| 003 | Entitlement cache strategy |
| 004 | Celery vs outbox relay boundary |
| 005 | PaymentProviderPort and mock Stripe |
| 006 | Append-only ledger |
| 007 | Reconciliation as first-class |
| 008 | Dunning from Stage 2 |
| 009 | Zero-downtime migrations |
| 010 | Identifier policy (UUIDv7, dual-id, outbox BIGINT) |
| 013 | Prometheus/Grafana — **scoped Adopt** (LGTP profile `observability`; amended 2026-03-02) |
| 014 | HTTP `idempotency_responses` — Defer Accepted |




### E. In-repo links

- OpenAPI: `/docs`
- ADR: `docs/adr/`
- Runbooks: `docs/runbooks/`
- SLO: `docs/slo.md`
- Dashboards: [`deploy/observability/grafana/provisioning/dashboards/`](deploy/observability/grafana/provisioning/dashboards/) (profile `observability`; Helm dashboards optional)



### F. “Addition → KPI” matrix


| Addition | KPI |
| -------- | --- |
| Entitlement evaluation + short cache TTL | MRR leakage < 0.2%; limit tickets |
| Persist-first webhooks + outbox | 0% webhook loss; processing SLA |
| Ledger + reconciliation | Reconciliation accuracy ≥ 99.5% |
| Grace + dunning (Stage 2) | Involuntary churn < 5% |
| Rate limiting + health/shutdown | Availability; abuse protection |
| Catalog publish without code | Time-to-plan < 2 h |


---

**End of document.**
Next step: approve specification → plan Stage 1 sprint → scaffold repository per §9 (including thin `demo_ui`).
