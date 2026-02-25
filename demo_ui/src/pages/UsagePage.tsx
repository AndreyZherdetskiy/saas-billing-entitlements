import { useCallback, useState } from "react";
import { evaluateEntitlements, fetchUsageAggregates } from "../api/billing";
import { formatApiError } from "../api/errors";
import { AsyncState, Field, JsonDisplay, StatusBanner } from "../components/Display";
import { PageHelp } from "../components/PageHelp";
import { getDemoFeatureKeys, getDemoOrgId } from "../config";
import type { EvaluateResponse, UsageAggregatesResponse } from "../types/api";
import { ApiError } from "../types/api";

export function UsagePage() {
  const [orgId, setOrgId] = useState(getDemoOrgId());
  const [featureKeysText, setFeatureKeysText] = useState(
    getDemoFeatureKeys().join(", "),
  );
  const [aggregates, setAggregates] = useState<UsageAggregatesResponse | null>(null);
  const [evaluate, setEvaluate] = useState<EvaluateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [aggregateError, setAggregateError] = useState<string | null>(null);
  const [evaluateError, setEvaluateError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!orgId.trim()) {
      setAggregateError("Organization public_id is required");
      setEvaluateError(null);
      setAggregates(null);
      setEvaluate(null);
      return;
    }

    const featureKeys = featureKeysText
      .split(",")
      .map((key) => key.trim())
      .filter((key) => key.length > 0);
    if (featureKeys.length === 0) {
      setEvaluateError("At least one feature_key is required");
      setAggregates(null);
      setEvaluate(null);
      return;
    }

    setLoading(true);
    setAggregateError(null);
    setEvaluateError(null);

    try {
      const usage = await fetchUsageAggregates(orgId.trim());
      setAggregates(usage);
    } catch (err) {
      setAggregates(null);
      if (err instanceof ApiError && err.status === 404) {
        setAggregateError(
          "Organization not found (404). Check organization public_id, or use entitlements evaluate used/remaining as a read-only proxy.",
        );
      } else {
        setAggregateError(formatApiError(err));
      }
    }

    try {
      const response = await evaluateEntitlements(orgId.trim(), featureKeys);
      setEvaluate(response);
    } catch (err) {
      setEvaluate(null);
      setEvaluateError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [featureKeysText, orgId]);

  return (
    <div className="page">
      <header className="page-header">
        <h2>Usage</h2>
        <p>
          Display-only meters for the current period. Ingest is{" "}
          <code>POST /v1/usage/events/batch</code> — not called from this UI.
        </p>
      </header>

      <PageHelp>
        <p>
          Prefers <code>GET …/usage</code> aggregates when the API exposes them. If
          that route is missing (404), the screen falls back to entitlements evaluate
          <code>used</code>/<code>remaining</code> as a read-only proxy so demos still
          show consumption.
        </p>
        <ul>
          <li>
            To create usage: call the batch ingest API (or seed scripts), then reload
            this page.
          </li>
          <li>Feature keys must match catalog keys attached to the org’s plan.</li>
        </ul>
      </PageHelp>

      <div className="toolbar">
        <Field
          label="Organization public_id"
          value={orgId}
          onChange={setOrgId}
          placeholder="UUID"
        />
        <Field
          label="Feature keys (comma-separated)"
          value={featureKeysText}
          onChange={setFeatureKeysText}
          placeholder="api_calls, seats"
        />
        <button type="button" onClick={() => void load()}>
          Load usage view
        </button>
      </div>

      <AsyncState loading={loading} error={evaluateError} />
      {aggregateError ? (
        <StatusBanner tone={aggregateError.includes("not implemented") ? "info" : "warning"}>
          {aggregateError}
        </StatusBanner>
      ) : null}

      {aggregates?.aggregates.length || evaluate?.results.length ? (
        <section className="panel">
          <h2>Usage by feature</h2>
          <table className="data-table">
            <thead>
              <tr>
                <th>Feature</th>
                {aggregates ? (
                  <>
                    <th>Period start</th>
                    <th>Period end</th>
                    <th>Quantity</th>
                  </>
                ) : (
                  <>
                    <th>Used</th>
                    <th>Limit</th>
                    <th>Remaining</th>
                    <th>Allowed</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {aggregates
                ? aggregates.aggregates.map((row) => (
                    <tr key={`${row.feature_key}-${row.period_start}`}>
                      <td>{row.feature_key}</td>
                      <td>{new Date(row.period_start).toLocaleString()}</td>
                      <td>{new Date(row.period_end).toLocaleString()}</td>
                      <td>{row.quantity}</td>
                    </tr>
                  ))
                : evaluate?.results.map((row) => (
                    <tr key={row.feature_key}>
                      <td>{row.feature_key}</td>
                      <td>{row.used ?? "—"}</td>
                      <td>{row.limit ?? "—"}</td>
                      <td>{row.remaining ?? "—"}</td>
                      <td>{String(row.allowed)}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {evaluate ? (
        <JsonDisplay
          value={{
            aggregates_api: aggregates,
            entitlements_evaluate: evaluate,
            note: "No billing logic in browser — server-computed values only.",
          }}
          title="Raw API responses"
        />
      ) : null}
    </div>
  );
}
