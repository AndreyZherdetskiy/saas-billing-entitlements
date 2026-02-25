export interface Organization {
  public_id: string;
  external_id: string | null;
  name: string;
  billing_email: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Subscription {
  public_id: string;
  organization_public_id: string;
  plan_id: string;
  status: string;
  current_period_start: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  trial_end: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface EvaluateResult {
  feature_key: string;
  allowed: boolean;
  limit: number | null;
  used: number | null;
  remaining: number | null;
  reason: string | null;
}

export interface EvaluateResponse {
  organization_public_id: string;
  subscription_status: string;
  results: EvaluateResult[];
  cache_hit: boolean;
  evaluated_at: string;
  version: number;
}

export interface HealthReadyResponse {
  status: "ok" | "degraded" | "unavailable";
  reasons: string[];
  checks: Record<string, "ok" | "fail">;
}

export interface UsageAggregateRow {
  feature_key: string;
  period_start: string;
  period_end: string;
  quantity: number;
}

export interface UsageAggregatesResponse {
  organization_public_id: string;
  aggregates: UsageAggregateRow[];
}

export interface ReconciliationRun {
  id: string;
  run_type: string;
  status: string;
  stats: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
}

export interface ReconciliationDiscrepancy {
  id: string;
  run_id: string;
  kind: string;
  external_invoice_id: string | null;
  expected_amount_cents: number | null;
  actual_amount_cents: number | null;
  delta_cents: number | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface DunningCampaign {
  id: string;
  subscription_public_id: string;
  organization_public_id: string;
  status: string;
  grace_until: string | null;
  started_at: string;
  created_at: string;
}

export interface ApiErrorBody {
  detail?: string | { msg: string }[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}
