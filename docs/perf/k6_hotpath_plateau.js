/**
 * Laptop evaluate characterization plateaus (not spec profile A/E).
 *
 * Open model: constant-arrival-rate
 * https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/
 *
 * One plateau per run. Characterization: TARGET_RPS=15, 40, 80, 150 × ~22s.
 * Ceiling hunt: 200, 300, 400, 500… with MAX_VUS ≥ 400 until break.
 * Do not treat results as §8.1.1 profile A DoD (3 000 RPS, ≥3 replicas).
 *
 * Env (required): K6_API_KEY, K6_ORG_ID
 * Env (optional): BASE_URL, K6_FEATURE_KEY, TARGET_RPS (default 15),
 *                 DURATION (default 22s), MAX_VUS (default 80)
 *
 * Docker (Compose network; stdin via scripts/run_k6_docker.sh):
 *   TARGET_RPS=15 DURATION=22s MAX_VUS=400 ./scripts/run_k6_docker.sh k6_hotpath_plateau.js
 */

import http from 'k6/http';
import { check } from 'k6';

const baseUrl = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const apiKey = __ENV.K6_API_KEY;
const orgId = __ENV.K6_ORG_ID;
const featureKey = __ENV.K6_FEATURE_KEY || 'api_calls';
const targetRps = Number(__ENV.TARGET_RPS || 15);
const duration = __ENV.DURATION || '22s';
const maxVUs = Number(__ENV.MAX_VUS || 80);
const preAllocatedVUs = Math.min(maxVUs, Math.max(8, Math.ceil(targetRps * 1.5)));

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

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    plateau: {
      executor: 'constant-arrival-rate',
      rate: targetRps,
      timeUnit: '1s',
      duration,
      preAllocatedVUs,
      maxVUs,
      gracefulStop: '8s',
    },
  },
  // Characterization: keep thresholds loose so the summary is recorded even
  // when a plateau saturates. Stop the sequence on error/drop storm in the
  // report — do not raise TARGET_RPS toward stand profile A on a laptop.
  thresholds: {
    http_req_failed: ['rate<0.5'],
    dropped_iterations: ['count<10000'],
  },
};

export function setup() {
  const res = http.post(evaluateUrl, payload, { headers, tags: { phase: 'warmup' } });
  if (res.status !== 200) {
    throw new Error(`Warmup evaluate failed: status=${res.status}`);
  }
  return { targetRps };
}

export default function () {
  const res = http.post(evaluateUrl, payload, { headers, tags: { phase: 'load' } });
  check(res, { 'status is 200': (r) => r.status === 200 });
}
