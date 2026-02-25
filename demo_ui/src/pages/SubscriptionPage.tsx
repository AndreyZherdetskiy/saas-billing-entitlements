import { useCallback, useEffect, useState } from "react";
import { fetchSubscriptions } from "../api/billing";
import { formatApiError } from "../api/errors";
import { AsyncState, Field, JsonDisplay, StatusBanner } from "../components/Display";
import { PageHelp } from "../components/PageHelp";
import { getDemoOrgId } from "../config";
import type { Subscription } from "../types/api";

export function SubscriptionPage() {
  const [orgId, setOrgId] = useState(getDemoOrgId());
  const [data, setData] = useState<Subscription[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const load = useCallback(async () => {
    if (!orgId.trim()) {
      setError("Organization public_id is required");
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const subscriptions = await fetchSubscriptions(orgId.trim());
      setData(subscriptions);
    } catch (err) {
      setData(null);
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }
    const timer = window.setInterval(() => {
      void load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const primary = data?.[0] ?? null;

  return (
    <div className="page">
      <header className="page-header">
        <h2>Subscription</h2>
        <p>GET /v1/organizations/{"{organization_public_id}"}/subscriptions</p>
      </header>

      <PageHelp>
        <p>
          Shows plan subscriptions for the org and their lifecycle{" "}
          <code>status</code> (<code>trialing</code>, <code>active</code>,{" "}
          <code>past_due</code>, <code>canceled</code>, …). Status changes after
          payment webhooks (mock-stripe → API), not inside this UI.
        </p>
        <ul>
          <li>
            Enable <strong>Auto-refresh</strong> while you trigger{" "}
            <code>invoice.paid</code> / <code>payment_failed</code> on mock-stripe
            to watch status flip.
          </li>
          <li>
            First row is treated as the “primary” subscription for the status banner.
          </li>
        </ul>
      </PageHelp>

      <div className="toolbar">
        <Field
          label="Organization public_id"
          value={orgId}
          onChange={setOrgId}
          placeholder="UUID"
        />
        <button type="button" onClick={() => void load()}>
          Fetch
        </button>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          Auto-refresh every 3s (after webhook)
        </label>
      </div>

      <AsyncState loading={loading} error={error} />

      {primary ? (
        <StatusBanner tone={primary.status === "active" ? "success" : "info"}>
          Current status: <strong>{primary.status}</strong> — updated{" "}
          {new Date(primary.updated_at).toLocaleString()}
        </StatusBanner>
      ) : null}

      {data ? <JsonDisplay value={data} title="API response" /> : null}
    </div>
  );
}
