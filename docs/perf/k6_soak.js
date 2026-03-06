/**
 * Profile D — Soak (spec §8.1.1, recommended)
 *
 * Target: ~0.3× peak C — evaluate 900 + usage 450 + admin 150 for 30–60 min.
 * Mix mirrors profile C ratios at lower intensity:
 *   evaluate   900 RPS
 *   usage      450 RPS (1 event / request)
 *   admin read 150 RPS
 * Acceptance: stable p99/heap; no unbounded unpublished outbox growth
 * (outbox lag measured outside k6 — see runbook / metrics).
 *
 * Env (required): K6_API_KEY, K6_ORG_ID
 * Env (optional): BASE_URL, K6_PROFILE=smoke|full, K6_FEATURE_KEY,
 *                 K6_SOAK_DURATION (override full duration, e.g. 45m / 60m)
 *
 * Examples:
 *   K6_PROFILE=smoke k6 run docs/perf/k6_soak.js
 *   K6_PROFILE=full  k6 run docs/perf/k6_soak.js
 *   K6_PROFILE=full K6_SOAK_DURATION=60m k6 run docs/perf/k6_soak.js
 */

import http from 'k6/http';
import { check } from 'k6';

const profile = __ENV.K6_PROFILE || 'smoke';
const baseUrl = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const apiKey = __ENV.K6_API_KEY;
const orgId = __ENV.K6_ORG_ID;
const featureKey = __ENV.K6_FEATURE_KEY || 'api_calls';
const soakDurationOverride = __ENV.K6_SOAK_DURATION;

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

const profiles = {
  smoke: {
    duration: '2m',
    gracefulStop: '10s',
    evaluate: { rate: 9, preAllocatedVUs: 8, maxVUs: 12 },
    usage: { rate: 4, preAllocatedVUs: 5, maxVUs: 8 },
    adminRead: { rate: 2, preAllocatedVUs: 3, maxVUs: 6 },
  },
  full: {
    duration: soakDurationOverride || '30m',
    gracefulStop: '30s',
    evaluate: { rate: 900, preAllocatedVUs: 200, maxVUs: 1200 },
    usage: { rate: 450, preAllocatedVUs: 150, maxVUs: 900 },
    adminRead: { rate: 150, preAllocatedVUs: 50, maxVUs: 300 },
  },
};

const selected = profiles[profile];
if (!selected) {
  throw new Error(`Unknown K6_PROFILE=${profile}; use smoke or full.`);
}

export const options = {
  discardResponseBodies: true,
  scenarios: {
    soak_evaluate: {
      executor: 'constant-arrival-rate',
      exec: 'evaluate',
      rate: selected.evaluate.rate,
      timeUnit: '1s',
      duration: selected.duration,
      preAllocatedVUs: selected.evaluate.preAllocatedVUs,
      maxVUs: selected.evaluate.maxVUs,
      gracefulStop: selected.gracefulStop,
    },
    soak_usage: {
      executor: 'constant-arrival-rate',
      exec: 'usageBatch',
      rate: selected.usage.rate,
      timeUnit: '1s',
      duration: selected.duration,
      preAllocatedVUs: selected.usage.preAllocatedVUs,
      maxVUs: selected.usage.maxVUs,
      gracefulStop: selected.gracefulStop,
    },
    soak_admin_read: {
      executor: 'constant-arrival-rate',
      exec: 'adminUsageRead',
      rate: selected.adminRead.rate,
      timeUnit: '1s',
      duration: selected.duration,
      preAllocatedVUs: selected.adminRead.preAllocatedVUs,
      maxVUs: selected.adminRead.maxVUs,
      gracefulStop: selected.gracefulStop,
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
          'http_req_failed{path:evaluate}': ['rate<0.01'],
          'http_req_duration{path:evaluate}': ['p(99)<200'],
          'http_req_failed{path:usage}': ['rate<0.01'],
          'http_req_failed{path:admin_read}': ['rate<0.01'],
          dropped_iterations: ['count==0'],
        },
};

export function setup() {
  const res = http.post(evaluateUrl, evaluatePayload, {
    headers,
    tags: { path: 'evaluate', phase: 'warmup' },
  });
  if (res.status !== 200) {
    throw new Error(`Warmup evaluate failed: status=${res.status}`);
  }
  return {};
}

export function evaluate() {
  const res = http.post(evaluateUrl, evaluatePayload, {
    headers,
    tags: { path: 'evaluate', phase: 'load' },
  });
  check(res, { 'evaluate 200': (r) => r.status === 200 });
}

export function usageBatch() {
  const body = JSON.stringify({
    organization_public_id: orgId,
    events: [
      {
        feature_key: featureKey,
        quantity: 1,
        idempotency_key: `k6-d-${__VU}-${__ITER}-${Date.now()}`,
      },
    ],
  });
  const res = http.post(usageBatchUrl, body, {
    headers,
    tags: { path: 'usage', phase: 'load' },
  });
  check(res, { 'usage 200': (r) => r.status === 200 });
}

export function adminUsageRead() {
  const res = http.get(usageReadUrl, {
    headers: { Authorization: headers.Authorization },
    tags: { path: 'admin_read', phase: 'load' },
  });
  check(res, { 'admin read 200': (r) => r.status === 200 });
}

export default function () {
  evaluate();
}
