import { useCallback, useState } from "react";
import { fetchDunningCampaigns, fetchSubscriptions } from "../api/billing";
import { formatApiError } from "../api/errors";
import { AsyncState, Field, JsonDisplay, StatusBanner } from "../components/Display";
import { PageHelp } from "../components/PageHelp";
import { getDemoOrgId, getPlatformAdminApiKey } from "../config";
import type { DunningCampaign, Subscription } from "../types/api";

export function DunningPage() {
  const [orgId, setOrgId] = useState(getDemoOrgId());
  const [campaigns, setCampaigns] = useState<DunningCampaign[] | null>(null);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(false);
  const [campaignsError, setCampaignsError] = useState<string | null>(null);
  const [subscriptionError, setSubscriptionError] = useState<string | null>(null);

  const adminConfigured = Boolean(getPlatformAdminApiKey());

  const load = useCallback(async () => {
    setLoading(true);
    setCampaignsError(null);
    setSubscriptionError(null);

    const trimmedOrgId = orgId.trim();

    if (trimmedOrgId) {
      try {
        const subscriptions = await fetchSubscriptions(trimmedOrgId);
        setSubscription(subscriptions[0] ?? null);
      } catch (err) {
        setSubscription(null);
        setSubscriptionError(formatApiError(err));
      }
    } else {
      setSubscription(null);
    }

    if (!adminConfigured) {
      setCampaigns(null);
      setCampaignsError(
        "platform_admin API key required for GET /v1/admin/dunning/campaigns",
      );
      setLoading(false);
      return;
    }

    try {
      const response = await fetchDunningCampaigns(
        trimmedOrgId ? { organizationPublicId: trimmedOrgId } : undefined,
      );
      setCampaigns(response);
    } catch (err) {
      setCampaigns(null);
      setCampaignsError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [adminConfigured, orgId]);

  const primaryCampaign = campaigns?.[0] ?? null;

  return (
    <div className="page">
      <header className="page-header">
        <h2>Dunning campaign</h2>
        <p>
          Display-only card for payment-recovery campaigns after{" "}
          <code>payment_failed</code>.
        </p>
      </header>

      <PageHelp>
        <p>
          When a subscription enters <code>past_due</code>, the platform may open a
          dunning campaign (grace window + retry schedule). This screen lists campaigns
          and shows subscription context — it does <strong>not</strong> pause/resume
          (use Admin API for that).
        </p>
        <ul>
          <li>
            Filter by org public_id (optional). Empty filter lists all campaigns the
            admin key can see.
          </li>
          <li>
            Demo: trigger a failed payment via mock-stripe, then reload — expect{" "}
            <code>past_due</code> and/or an active campaign when dunning is enabled.
          </li>
        </ul>
      </PageHelp>

      <div className="toolbar">
        <Field
          label="Organization public_id (filter)"
          value={orgId}
          onChange={setOrgId}
          placeholder="UUID"
        />
        <button type="button" onClick={() => void load()}>
          Load dunning view
        </button>
      </div>

      {!adminConfigured ? (
        <StatusBanner tone="warning">
          Set <code>DEMO_UI_PLATFORM_ADMIN_API_KEY</code> for dunning admin routes.
        </StatusBanner>
      ) : null}

      <AsyncState loading={loading} error={subscriptionError} />

      {subscription ? (
        <StatusBanner tone={subscription.status === "past_due" ? "warning" : "info"}>
          Subscription status: <strong>{subscription.status}</strong>
          {subscription.status === "past_due"
            ? " — dunning may be active after payment_failed webhook"
            : null}
        </StatusBanner>
      ) : null}

      {campaignsError ? (
        <StatusBanner tone="error">{campaignsError}</StatusBanner>
      ) : null}

      {primaryCampaign ? (
        <section className="panel dunning-card">
          <h2>Campaign</h2>
          <dl className="detail-grid">
            <div>
              <dt>Status</dt>
              <dd>{primaryCampaign.status}</dd>
            </div>
            <div>
              <dt>Grace until</dt>
              <dd>
                {primaryCampaign.grace_until
                  ? new Date(primaryCampaign.grace_until).toLocaleString()
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{new Date(primaryCampaign.started_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Subscription</dt>
              <dd>{primaryCampaign.subscription_public_id}</dd>
            </div>
            <div>
              <dt>Campaign id</dt>
              <dd>{primaryCampaign.id}</dd>
            </div>
          </dl>
        </section>
      ) : campaigns && campaigns.length === 0 && !campaignsError ? (
        <StatusBanner tone="info">
          No dunning campaigns for this organization (or none active).
        </StatusBanner>
      ) : null}

      {campaigns && campaigns.length > 1 ? (
        <section className="panel">
          <h2>All campaigns ({campaigns.length})</h2>
          <table className="data-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Grace until</th>
                <th>Started</th>
                <th>Subscription</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((campaign) => (
                <tr key={campaign.id}>
                  <td>{campaign.status}</td>
                  <td>
                    {campaign.grace_until
                      ? new Date(campaign.grace_until).toLocaleString()
                      : "—"}
                  </td>
                  <td>{new Date(campaign.started_at).toLocaleString()}</td>
                  <td>{campaign.subscription_public_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {campaigns || subscription ? (
        <JsonDisplay
          value={{
            subscription,
            campaigns,
            note: "Pause/resume (POST) intentionally not wired — display-only in this console.",
          }}
          title="Raw API responses"
        />
      ) : null}
    </div>
  );
}
