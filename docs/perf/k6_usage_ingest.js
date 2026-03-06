/**
 * Profile B — Usage ingest write path (spec §8.1.1)
 *
 * Target: ~1 500 usage events/s for 10 min (batch ≤1000; NFR stage 3 write path).
 * Acceptance: 2xx; idempotent duplicates OK; no 5xx storm; PG without queue storm.
 *
 * Full intensity: batch_size=100 → 15 batch RPS = 1 500 events/s.
 *
 * Env (required):
 *   K6_API_KEY      — Bearer (product_service or platform_admin)
 *   K6_ORG_ID       — organization public_id (UUID)
 *
 * Env (optional):
 *   BASE_URL           — default http://localhost:8000
 *   K6_PROFILE         — "smoke" | "full"
 *   K6_FEATURE_KEY     — default api_calls
 *   K6_BATCH_SIZE      — events per request (default 100; max 1000)
 *
 * Examples:
 *   K6_PROFILE=smoke k6 run docs/perf/k6_usage_ingest.js
 *   K6_PROFILE=full  k6 run docs/perf/k6_usage_ingest.js
 */

import http from 'k6/http';
import { check } from 'k6';

const profile = __ENV.K6_PROFILE || 'smoke';
const baseUrl = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const apiKey = __ENV.K6_API_KEY;
const orgId = __ENV.K6_ORG_ID;
const featureKey = __ENV.K6_FEATURE_KEY || 'api_calls';
const batchSize = Math.min(1000, Math.max(1, Number(__ENV.K6_BATCH_SIZE || 100)));

if (!apiKey || !orgId) {
  throw new Error('Set K6_API_KEY and K6_ORG_ID before running.');
}

const usageBatchUrl = `${baseUrl}/v1/usage/events/batch`;
const headers = {
  Authorization: `Bearer ${apiKey}`,
  'Content-Type': 'application/json',
};

/** events/s ÷ batch_size = HTTP batch RPS */
const profiles = {
  smoke: {
    eventsPerSec: 100,
    duration: '30s',
    preAllocatedVUs: 8,
    maxVUs: 20,
    gracefulStop: '10s',
  },
  full: {
    eventsPerSec: 1500,
    duration: '10m',
    preAllocatedVUs: 200,
    maxVUs: 1500,
    gracefulStop: '30s',
  },
};

const selected = profiles[profile];
if (!selected) {
  throw new Error(`Unknown K6_PROFILE=${profile}; use smoke or full.`);
}

const batchRate = Math.max(1, Math.ceil(selected.eventsPerSec / batchSize));

export const options = {
  discardResponseBodies: true,
  scenarios: {
    usage_ingest: {
      executor: 'constant-arrival-rate',
      rate: batchRate,
      timeUnit: '1s',
      duration: selected.duration,
      preAllocatedVUs: selected.preAllocatedVUs,
      maxVUs: selected.maxVUs,
      gracefulStop: selected.gracefulStop,
    },
  },
  thresholds:
    profile === 'smoke'
      ? {
          http_req_failed: ['rate<0.05'],
          'http_req_duration{path:usage}': ['p(99)<5000'],
          dropped_iterations: ['count<100'],
        }
      : {
          http_req_failed: ['rate<0.01'],
          'http_req_duration{path:usage}': ['p(99)<500'],
          dropped_iterations: ['count==0'],
        },
};

function buildBatchBody() {
  const stamp = `${__VU}-${__ITER}-${Date.now()}`;
  const events = [];
  for (let i = 0; i < batchSize; i += 1) {
    events.push({
      feature_key: featureKey,
      quantity: 1,
      idempotency_key: `k6-b-${stamp}-${i}`,
    });
  }
  return JSON.stringify({
    organization_public_id: orgId,
    events,
  });
}

export function setup() {
  const body = JSON.stringify({
    organization_public_id: orgId,
    events: [
      {
        feature_key: featureKey,
        quantity: 1,
        idempotency_key: `warmup-b-${Date.now()}`,
      },
    ],
  });
  const res = http.post(usageBatchUrl, body, {
    headers,
    tags: { path: 'usage', phase: 'warmup' },
  });
  if (res.status !== 200) {
    throw new Error(`Warmup usage batch failed: status=${res.status} body=${res.body}`);
  }
  return {
    batchRate,
    batchSize,
    eventsPerSec: batchRate * batchSize,
  };
}

export default function () {
  const res = http.post(usageBatchUrl, buildBatchBody(), {
    headers,
    tags: { path: 'usage', phase: 'load' },
  });
  check(res, {
    'usage batch status 200': (r) => r.status === 200,
  });
}
