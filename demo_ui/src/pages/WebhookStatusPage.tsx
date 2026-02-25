import { useCallback, useEffect, useState } from "react";
import {
  fetchHealthLive,
  fetchHealthReady,
  fetchSubscriptions,
} from "../api/billing";
import { formatApiError } from "../api/errors";
import { AsyncState, Field, JsonDisplay, StatusBanner } from "../components/Display";
import { PageHelp } from "../components/PageHelp";
import { getDemoOrgId } from "../config";
import type { HealthReadyResponse, Subscription } from "../types/api";

interface WebhookStatusView {
  live: { status: string } | null;
  ready: HealthReadyResponse | null;
  subscription: Subscription | null;
  polledAt: string | null;
}

export function WebhookStatusPage() {
  const [orgId, setOrgId] = useState(getDemoOrgId());
  const [data, setData] = useState<WebhookStatusView>({
    live: null,
    ready: null,
    subscription: null,
    polledAt: null,
  });
  const [loading, setLoading] = useState(false);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [subscriptionError, setSubscriptionError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setHealthError(null);
    setSubscriptionError(null);

    let live: { status: string } | null = null;
    let ready: HealthReadyResponse | null = null;
    let healthFetched = false;

    try {
      [live, ready] = await Promise.all([fetchHealthLive(), fetchHealthReady()]);
      healthFetched = true;
    } catch (err) {
      setHealthError(formatApiError(err));
    }

    let subscription: Subscription | null = null;
    let subscriptionFetched = false;
    const trimmedOrgId = orgId.trim();

    if (trimmedOrgId) {
      try {
        const subscriptions = await fetchSubscriptions(trimmedOrgId);
        subscription = subscriptions[0] ?? null;
        subscriptionFetched = true;
      } catch (err) {
        setSubscriptionError(formatApiError(err));
      }
    }

    setData((prev) => ({
      live: healthFetched ? live : prev.live,
      ready: healthFetched ? ready : prev.ready,
      subscription: subscriptionFetched
        ? subscription
        : trimmedOrgId
          ? prev.subscription
          : null,
      polledAt: new Date().toISOString(),
    }));

    setLoading(false);
  }, [orgId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }
    const timer = window.setInterval(() => {
      void load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const subscriptionStatus = data.subscription?.status ?? "unknown";

  return (
    <div className="page">
      <header className="page-header">
        <h2>Webhook status</h2>
        <p>
          Platform health + subscription status after webhook processing (indirect
          signal).
        </p>
      </header>

      <PageHelp>
        <p>
          There is no full webhook inbox in this UI yet (
          <code>GET /v1/admin/webhooks</code> planned). Instead we poll{" "}
          <code>/health/*</code> and the org’s subscription — after mock-stripe posts
          signed events, status should move (e.g. to <code>active</code> on{" "}
          <code>invoice.paid</code>).
        </p>
        <ul>
          <li>
            Keep auto-refresh on while you fire events from mock-stripe (
            <code>:8001</code>).
          </li>
          <li>
            Ready probe must stay green (postgres / redis / kafka) for a healthy demo.
          </li>
        </ul>
      </PageHelp>

      <div className="toolbar">
        <Field
          label="Organization public_id"
          value={orgId}
          onChange={setOrgId}
          placeholder="UUID to observe subscription status"
        />
        <button type="button" onClick={() => void load()}>
          Refresh
        </button>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          Auto-refresh every 3s
        </label>
      </div>

      <AsyncState loading={loading} error={healthError} />
      {subscriptionError ? (
        <StatusBanner tone="error">
          Subscription fetch failed: {subscriptionError}
        </StatusBanner>
      ) : null}

      <StatusBanner tone={subscriptionStatus === "active" ? "success" : "warning"}>
        Subscription status (webhook outcome): <strong>{subscriptionStatus}</strong>
        {data.polledAt ? ` — polled ${new Date(data.polledAt).toLocaleTimeString()}` : null}
      </StatusBanner>

      <JsonDisplay
        value={{
          note: "Trigger invoice.paid via mock-stripe; subscription.status should become active.",
          health_live: data.live,
          health_ready: data.ready,
          subscription: data.subscription,
          polled_at: data.polledAt,
        }}
        title="Observed API state"
      />
    </div>
  );
}
