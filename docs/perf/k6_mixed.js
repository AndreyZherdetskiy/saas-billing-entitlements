/**
 * Profile C — Mixed prod-like (spec §8.1.1)
 *
 * Target: 5 000 HTTP RPS mix for 10 min (band 4 500–6 000) — evaluate + usage ingest + light admin read.
 * Acceptance: profile A/B SLA on respective paths; outbox_lag_seconds p99 < 30 s under peak
 * (measured via metrics during the run, not k6 thresholds).
 *
 * Mix (full profile, 5 000 RPS):
 *   evaluate  3 000 RPS  POST /v1/entitlements/evaluate
 *   usage     1 500 RPS  POST /v1/usage/events/batch (1 event per request)
 *   admin read  500 RPS  GET  /v1/organizations/{org}/usage
 *
 * Env (required):
 *   K6_API_KEY      — Bearer API key (platform_admin recommended)
 *   K6_ORG_ID       — organization public_id (UUID)
 *
 * Env (optional):
 *   BASE_URL        — default http://localhost:8000
 *   K6_PROFILE      — "smoke" (short/low RPS) | "full" (§8.1.1 profile C)
 *   K6_FEATURE_KEY  — default api_calls
 *
 * Examples:
 *   K6_PROFILE=smoke k6 run docs/perf/k6_mixed.js
 *   K6_PROFILE=full k6 run docs/perf/k6_mixed.js
 *
 * Docker (Compose network; script on stdin — Grafana Docker docs):
 *   docker run --rm -i --network billing-platform \
 *     -e K6_API_KEY -e K6_ORG_ID -e BASE_URL=http://billing-api:8000 \
 *     -e K6_PROFILE=smoke \
 *     grafana/k6 run - < docs/perf/k6_mixed.js
 */

import http from 'k6/http';
import { check } from 'k6';

const profile = __ENV.K6_PROFILE || 'smoke';
const baseUrl = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const apiKey = __ENV.K6_API_KEY;
const orgId = __ENV.K6_ORG_ID;
const featureKey = __ENV.K6_FEATURE_KEY || 'api_calls';

if (!apiKey || !orgId) {
  throw new Error('Set K6_API_KEY and K6_ORG_ID before running.');
}

const evaluateUrl = `${baseUrl}/v1/entitlements/evaluate`;
const usageBatchUrl = `${baseUrl}/v1/usage/events/batch`;
const usageReadUrl = `${baseUrl}/v1/organizations/${orgId}/usage`;

const evaluatePayload = JSON.stringify({
  organization_public_id: orgId,
  checks: [{ feature_key: featureKey, quantity: 1 }],
});

const headers = {
  Authorization: `Bearer ${apiKey}`,
  'Content-Type': 'application/json',
};

/** Profile C full vs laptop smoke (spec §10.5). Smoke total ≤15 RPS (platform_admin key). */
const profiles = {
  smoke: {
    duration: '30s',
    gracefulStop: '10s',
    evaluate: { rate: 9, preAllocatedVUs: 8, maxVUs: 12 },
    usage: { rate: 4, preAllocatedVUs: 5, maxVUs: 8 },
    adminRead: { rate: 2, preAllocatedVUs: 3, maxVUs: 6 },
  },
  full: {
    duration: '10m',
    gracefulStop: '30s',
    evaluate: { rate: 3000, preAllocatedVUs: 600, maxVUs: 3500 },
    usage: { rate: 1500, preAllocatedVUs: 400, maxVUs: 2500 },
    adminRead: { rate: 500, preAllocatedVUs: 150, maxVUs: 1000 },
  },
};

const selected = profiles[profile];
if (!selected) {
  throw new Error(`Unknown K6_PROFILE=${profile}; use smoke or full.`);
}

const totalRps =
  selected.evaluate.rate + selected.usage.rate + selected.adminRead.rate;

export const options = {
  discardResponseBodies: true,
  scenarios: {
    mixed_evaluate: {
      executor: 'constant-arrival-rate',
      exec: 'evaluate',
      rate: selected.evaluate.rate,
      timeUnit: '1s',
      duration: selected.duration,
      preAllocatedVUs: selected.evaluate.preAllocatedVUs,
      maxVUs: selected.evaluate.maxVUs,
      gracefulStop: selected.gracefulStop,
    },
    mixed_usage: {
      executor: 'constant-arrival-rate',
      exec: 'usageBatch',
      rate: selected.usage.rate,
      timeUnit: '1s',
      duration: selected.duration,
      preAllocatedVUs: selected.usage.preAllocatedVUs,
      maxVUs: selected.usage.maxVUs,
      gracefulStop: selected.gracefulStop,
      startTime: '0s',
    },
    mixed_admin_read: {
      executor: 'constant-arrival-rate',
      exec: 'adminUsageRead',
      rate: selected.adminRead.rate,
      timeUnit: '1s',
      duration: selected.duration,
      preAllocatedVUs: selected.adminRead.preAllocatedVUs,
      maxVUs: selected.adminRead.maxVUs,
      gracefulStop: selected.gracefulStop,
      startTime: '0s',
    },
  },
  thresholds:
    profile === 'smoke'
      ? {
          'http_req_failed{path:evaluate}': ['rate<0.05'],
          'http_req_duration{path:evaluate}': ['p(99)<5000'],
          'http_req_failed{path:usage}': ['rate<0.05'],
          'http_req_failed{path:admin_read}': ['rate<0.05'],
          dropped_iterations: ['count<200'],
        }
      : {
          'http_req_failed{path:evaluate}': ['rate<0.001'],
          'http_req_duration{path:evaluate}': ['p(99)<50'],
          'http_req_failed{path:usage}': ['rate<0.01'],
          'http_req_failed{path:admin_read}': ['rate<0.01'],
          dropped_iterations: ['count==0'],
        },
};

/** Warm Redis entitlement cache and validate routes before sustained load. */
export function setup() {
  for (let i = 0; i < 5; i += 1) {
    const res = http.post(evaluateUrl, evaluatePayload, {
      headers,
      tags: { path: 'evaluate', phase: 'warmup' },
    });
    if (res.status !== 200) {
      throw new Error(`Warmup evaluate failed: status=${res.status} body=${res.body}`);
    }
  }

  const usageBody = JSON.stringify({
    organization_public_id: orgId,
    events: [
      {
        feature_key: featureKey,
        quantity: 1,
        idempotency_key: `warmup-usage-${Date.now()}`,
      },
    ],
  });
  const usageRes = http.post(usageBatchUrl, usageBody, {
    headers,
    tags: { path: 'usage', phase: 'warmup' },
  });
  if (usageRes.status !== 200) {
    throw new Error(`Warmup usage batch failed: status=${usageRes.status} body=${usageRes.body}`);
  }

  const readRes = http.get(usageReadUrl, {
    headers: { Authorization: headers.Authorization },
    tags: { path: 'admin_read', phase: 'warmup' },
  });
  if (readRes.status !== 200) {
    throw new Error(`Warmup usage read failed: status=${readRes.status} body=${readRes.body}`);
  }

  return { totalRps };
}

export function evaluate() {
  const res = http.post(evaluateUrl, evaluatePayload, {
    headers,
    tags: { path: 'evaluate', phase: 'load' },
  });
  check(res, {
    'evaluate status 200': (r) => r.status === 200,
  });
}

export function usageBatch() {
  const idempotencyKey = `k6-mixed-${__VU}-${__ITER}-${Date.now()}`;
  const body = JSON.stringify({
    organization_public_id: orgId,
    events: [
      {
        feature_key: featureKey,
        quantity: 1,
        idempotency_key: idempotencyKey,
      },
    ],
  });
  const res = http.post(usageBatchUrl, body, {
    headers,
    tags: { path: 'usage', phase: 'load' },
  });
  check(res, {
    'usage batch status 200': (r) => r.status === 200,
  });
}

export function adminUsageRead() {
  const res = http.get(usageReadUrl, {
    headers: { Authorization: headers.Authorization },
    tags: { path: 'admin_read', phase: 'load' },
  });
  check(res, {
    'usage read status 200': (r) => r.status === 200,
  });
}

export default function () {
  evaluate();
}
