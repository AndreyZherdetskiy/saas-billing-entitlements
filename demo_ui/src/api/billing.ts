import { apiGet, apiPost } from "../api/client";
import { getPlatformAdminApiKey } from "../config";
import type {
  DunningCampaign,
  EvaluateResponse,
  HealthReadyResponse,
  Organization,
  ReconciliationDiscrepancy,
  ReconciliationRun,
  Subscription,
  UsageAggregatesResponse,
} from "../types/api";

function adminAuth() {
  return { apiKey: getPlatformAdminApiKey() };
}

export function fetchOrganization(orgPublicId: string): Promise<Organization> {
  return apiGet<Organization>(`/v1/organizations/${orgPublicId}`);
}

export function fetchSubscriptions(orgPublicId: string): Promise<Subscription[]> {
  return apiGet<Subscription[]>(`/v1/organizations/${orgPublicId}/subscriptions`);
}

export function evaluateEntitlements(
  organizationId: string,
  featureKeys: string[],
): Promise<EvaluateResponse> {
  return apiPost<EvaluateResponse>("/v1/entitlements/evaluate", {
    organization_public_id: organizationId,
    checks: featureKeys.map((feature_key) => ({ feature_key, quantity: 1 })),
  });
}

export function fetchHealthLive(): Promise<{ status: string }> {
  return apiGet<{ status: string }>("/health/live");
}

export function fetchHealthReady(): Promise<HealthReadyResponse> {
  return apiGet<HealthReadyResponse>("/health/ready");
}

/** Spec GET /organizations/{org_id}/usage — may 404 until backend implements read path. */
export function fetchUsageAggregates(
  orgPublicId: string,
): Promise<UsageAggregatesResponse> {
  return apiGet<UsageAggregatesResponse>(
    `/v1/organizations/${encodeURIComponent(orgPublicId)}/usage`,
  );
}

export function fetchReconciliationRuns(
  limit = 50,
  offset = 0,
): Promise<ReconciliationRun[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return apiGet<ReconciliationRun[]>(
    `/v1/admin/reconciliation/runs?${params.toString()}`,
    adminAuth(),
  );
}

export function fetchReconciliationDiscrepancies(
  runId: string,
  limit = 100,
  offset = 0,
): Promise<ReconciliationDiscrepancy[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return apiGet<ReconciliationDiscrepancy[]>(
    `/v1/admin/reconciliation/runs/${encodeURIComponent(runId)}/discrepancies?${params.toString()}`,
    adminAuth(),
  );
}

/** Spec GET /admin/dunning/campaigns — optional org/subscription filters. */
export function fetchDunningCampaigns(options?: {
  organizationPublicId?: string;
  subscriptionPublicId?: string;
}): Promise<DunningCampaign[]> {
  const params = new URLSearchParams();
  if (options?.organizationPublicId) {
    params.set("organization_public_id", options.organizationPublicId);
  }
  if (options?.subscriptionPublicId) {
    params.set("subscription_public_id", options.subscriptionPublicId);
  }
  const query = params.toString();
  const path = query
    ? `/v1/admin/dunning/campaigns?${query}`
    : "/v1/admin/dunning/campaigns";
  return apiGet<DunningCampaign[]>(path, adminAuth());
}
