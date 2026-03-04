/**
 * Profile E — Ceiling / breaking point (spec §8.1.1, optional — not DoD)
 *
 * Grafana breakpoint: ramping-arrival-rate until abortOnFail.
 * https://grafana.com/docs/k6/latest/testing-guides/test-types/breakpoint-testing/
 * https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-arrival-rate/
 * https://grafana.com/docs/k6/latest/using-k6/thresholds/
 *
 * **8 000** RPS (`K6_CEILING_RPS` default) is a search upper bound for the full profile, not a constant hold.
 * Success = document the abort (or ramp-end) point; **not** a DoD gate.
 * Forbidden: treat 100k+ RPS laptop runs as prod validation; do not claim §8.1.1 profile A DoD.
 *
 * Env (required): K6_API_KEY, K6_ORG_ID
 * Env (optional): BASE_URL, K6_PROFILE=smoke|laptop|full, K6_FEATURE_KEY,
 *                 K6_CEILING_RPS (full ramp target, default 8000)
 *
 * Examples:
 *   K6_PROFILE=smoke  k6 run docs/perf/k6_ceiling.js
 *   K6_PROFILE=laptop k6 run docs/perf/k6_ceiling.js
 *   K6_PROFILE=full   k6 run docs/perf/k6_ceiling.js
 *
 * Smoke stays CI-safe (≤15 RPS). Laptop is Grafana breakpoint on this overlay
 * (ramp ~100 → ~2000; hundreds of VUs — not full 8k / 8000 VUs).
 * Makefile `load-*` sets API_RATE_LIMIT_*=0 — see docs/perf/README.md.
 */

import http from 'k6/http';
import { check } from 'k6';

const profile = __ENV.K6_PROFILE || 'smoke';
const baseUrl = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const apiKey = __ENV.K6_API_KEY;
const orgId = __ENV.K6_ORG_ID;
const featureKey = __ENV.K6_FEATURE_KEY || 'api_calls';
const ceilingRps = Number(__ENV.K6_CEILING_RPS || 8000);

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

const profiles = {
  // Smoke/CI: short ramp that plateaus at 15 RPS (rate-limit-safe smoke).
  smoke: {
    startRate: 5,
    stages: [
      { duration: '10s', target: 15 },
      { duration: '20s', target: 15 },
    ],
    preAllocatedVUs: 8,
    maxVUs: 20,
    gracefulStop: '10s',
    delayAbortEval: '10s',
    failRate: 'rate<0.5',
  },
  // This overlay: Grafana breakpoint — one ramp, abortOnFail (no plateau here).
  laptop: {
    startRate: 100,
    stages: [{ duration: '3m', target: 2000 }],
    preAllocatedVUs: 400,
    maxVUs: 800,
    gracefulStop: '15s',
    delayAbortEval: '20s',
    failRate: 'rate<0.05',
  },
  // Stand: ramp toward K6_CEILING_RPS until abortOnFail (Grafana breakpoint).
  full: {
    startRate: 0,
    stages: [{ duration: '3m', target: ceilingRps }],
    preAllocatedVUs: 1000,
    maxVUs: 8000,
    gracefulStop: '30s',
    delayAbortEval: '30s',
    failRate: 'rate<0.05',
  },
};

const selected = profiles[profile];
if (!selected) {
  throw new Error(`Unknown K6_PROFILE=${profile}; use smoke, laptop, or full.`);
}

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    ceiling_evaluate: {
      executor: 'ramping-arrival-rate',
      startRate: selected.startRate,
      timeUnit: '1s',
      stages: selected.stages,
      preAllocatedVUs: selected.preAllocatedVUs,
      maxVUs: selected.maxVUs,
      gracefulStop: selected.gracefulStop,
    },
  },
  thresholds: {
    http_req_failed: [
      {
        threshold: selected.failRate,
        abortOnFail: true,
        delayAbortEval: selected.delayAbortEval,
      },
    ],
    http_req_duration: ['p(99)<5000'],
    dropped_iterations: ['count<100000'],
  },
};

export function setup() {
  const res = http.post(evaluateUrl, payload, { headers, tags: { phase: 'warmup' } });
  if (res.status !== 200) {
    throw new Error(`Warmup evaluate failed: status=${res.status}`);
  }
  const lastStage = selected.stages[selected.stages.length - 1];
  return { targetRps: lastStage.target };
}

export default function () {
  const res = http.post(evaluateUrl, payload, { headers, tags: { phase: 'load' } });
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
}
