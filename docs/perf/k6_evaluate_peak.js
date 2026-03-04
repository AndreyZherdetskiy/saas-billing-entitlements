/**
 * Profile A — Evaluate peak (spec §8.1.1)
 *
 * Target: POST /v1/entitlements/evaluate at 3 000 RPS for 10 min (cached-heavy).
 * Acceptance: error rate < 0.1%; p99 latency < 50 ms at ≥3 API replicas.
 *
 * Env (required):
 *   K6_API_KEY      — Bearer API key (platform_admin or org-scoped)
 *   K6_ORG_ID       — organization public_id (UUID)
 *
 * Env (optional):
 *   BASE_URL        — default http://localhost:8000
 *   K6_PROFILE      — "smoke" (short/low RPS) | "full" (§8.1.1 profile A)
 *   K6_FEATURE_KEY  — default api_calls
 *
 * Examples:
 *   # Smoke on laptop (compose, 1 API replica)
 *   K6_PROFILE=smoke k6 run docs/perf/k6_evaluate_peak.js
 *
 *   # Full profile A on capable stand (≥3 API replicas, Helm/kind)
 *   K6_PROFILE=full k6 run docs/perf/k6_evaluate_peak.js
 *
 * Docker (Compose network; script on stdin — Grafana Docker docs):
 *   docker run --rm -i --network billing-platform \
 *     -e K6_API_KEY -e K6_ORG_ID -e BASE_URL=http://billing-api:8000 \
 *     -e K6_PROFILE=smoke \
 *     grafana/k6 run - < docs/perf/k6_evaluate_peak.js
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
const payload = JSON.stringify({
  organization_public_id: orgId,
  checks: [{ feature_key: featureKey, quantity: 1 }],
});

const headers = {
  Authorization: `Bearer ${apiKey}`,
  'Content-Type': 'application/json',
};

/** Profile A full vs laptop smoke (spec §10.5). Smoke ≤15 RPS; maxVUs under API pool 20+10. */
const profiles = {
  smoke: {
    rate: 15,
    duration: '30s',
    preAllocatedVUs: 8,
    maxVUs: 20,
    gracefulStop: '10s',
  },
  full: {
    rate: 3000,
    duration: '10m',
    preAllocatedVUs: 500,
    maxVUs: 3000,
    gracefulStop: '30s',
  },
};

const selected = profiles[profile];
if (!selected) {
  throw new Error(`Unknown K6_PROFILE=${profile}; use smoke or full.`);
}

export const options = {
  discardResponseBodies: true,
  scenarios: {
    evaluate_peak: {
      executor: 'constant-arrival-rate',
      rate: selected.rate,
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
          // Laptop smoke — script/route validation, not §8.1.1 SLO (see docs/perf/README.md).
          http_req_failed: ['rate<0.05'],
          http_req_duration: ['p(99)<5000'],
          dropped_iterations: ['count<200'],
        }
      : {
          // Profile A SLO (§8.1.1) — full stand with ≥3 replicas.
          http_req_failed: ['rate<0.001'],
          http_req_duration: ['p(99)<50'],
          dropped_iterations: ['count==0'],
        },
};

/** Warm Redis entitlement cache before sustained load. */
export function setup() {
  for (let i = 0; i < 5; i += 1) {
    const res = http.post(evaluateUrl, payload, { headers, tags: { phase: 'warmup' } });
    if (res.status !== 200) {
      throw new Error(`Warmup evaluate failed: status=${res.status} body=${res.body}`);
    }
  }
  return { evaluateUrl };
}

export default function () {
  const res = http.post(evaluateUrl, payload, { headers, tags: { phase: 'load' } });
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
}
