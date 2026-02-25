import { useCallback, useState } from "react";
import { evaluateEntitlements } from "../api/billing";
import { formatApiError } from "../api/errors";
import { AsyncState, Field, JsonDisplay, StatusBanner } from "../components/Display";
import { PageHelp } from "../components/PageHelp";
import { getDemoFeatureKeys, getDemoOrgId } from "../config";
import type { EvaluateResponse } from "../types/api";

export function EntitlementsPage() {
  const [orgId, setOrgId] = useState(getDemoOrgId());
  const [featureKeysText, setFeatureKeysText] = useState(
    getDemoFeatureKeys().join(", "),
  );
  const [data, setData] = useState<EvaluateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!orgId.trim()) {
      setError("Organization public_id is required");
      setData(null);
      return;
    }
    const featureKeys = featureKeysText
      .split(",")
      .map((key) => key.trim())
      .filter((key) => key.length > 0);
    if (featureKeys.length === 0) {
      setError("At least one feature_key is required");
      setData(null);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = await evaluateEntitlements(orgId.trim(), featureKeys);
      setData(response);
    } catch (err) {
      setData(null);
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [featureKeysText, orgId]);

  return (
    <div className="page">
      <header className="page-header">
        <h2>Entitlements evaluate</h2>
        <p>POST /v1/entitlements/evaluate — displays server response only</p>
      </header>

      <PageHelp>
        <p>
          Asks the API whether the org may use each feature (limits / boolean flags).
          This is the hot path product services call — Redis-backed cache versioning
          may return <code>cache_hit: true</code> on a second Evaluate.
        </p>
        <ul>
          <li>
            Feature keys come from the catalog (seed defaults:{" "}
            <code>api_calls</code>, <code>seats</code>).
          </li>
          <li>
            Response includes <code>allowed</code>, <code>used</code>/<code>limit</code>
            when metered, plus <code>subscription_status</code>.
          </li>
          <li>
            Evaluate twice — the second call should show a cache hit after the
            first.
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
        <Field
          label="Feature keys (comma-separated)"
          value={featureKeysText}
          onChange={setFeatureKeysText}
          placeholder="api_calls, seats"
        />
        <button type="button" onClick={() => void load()}>
          Evaluate
        </button>
      </div>

      <AsyncState loading={loading} error={error} />

      {data ? (
        <>
          <StatusBanner tone={data.cache_hit ? "success" : "info"}>
            subscription_status=<strong>{data.subscription_status}</strong>, cache_hit=
            <strong>{String(data.cache_hit)}</strong>, version={data.version}
          </StatusBanner>
          <JsonDisplay value={data} title="API response" />
        </>
      ) : null}
    </div>
  );
}
