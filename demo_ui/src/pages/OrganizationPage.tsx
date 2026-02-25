import { useCallback, useState } from "react";
import { fetchOrganization } from "../api/billing";
import { formatApiError } from "../api/errors";
import { AsyncState, Field, JsonDisplay, StatusBanner } from "../components/Display";
import { PageHelp } from "../components/PageHelp";
import { getDemoOrgId } from "../config";
import type { Organization } from "../types/api";

export function OrganizationPage() {
  const [orgId, setOrgId] = useState(getDemoOrgId());
  const [data, setData] = useState<Organization | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!orgId.trim()) {
      setError("Organization public_id is required");
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const organization = await fetchOrganization(orgId.trim());
      setData(organization);
    } catch (err) {
      setData(null);
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  return (
    <div className="page">
      <header className="page-header">
        <h2>Organization</h2>
        <p>GET /v1/organizations/{"{organization_public_id}"}</p>
      </header>

      <PageHelp>
        <p>
          Loads the tenant (customer org) by its public UUID. The value prefilled from{" "}
          <code>DEMO_UI_ORG_ID</code> is the org created by{" "}
          <code>scripts/seed_catalog.py</code> (or prod-like seed) — it is not a
          guarantee the API call succeeds if the API base/key is wrong.
        </p>
        <ul>
          <li>
            <strong>Auth:</strong> Bearer API key for that org or platform_admin.
          </li>
          <li>
            <strong>Typical failure:</strong> empty key, wrong UUID, or cross-origin
            API base (see banner above).
          </li>
          <li>
            Use the returned <code>public_id</code> / name to confirm which tenant
            you are demoing on other screens.
          </li>
        </ul>
      </PageHelp>

      <div className="toolbar">
        <Field
          label="Organization public_id"
          value={orgId}
          onChange={setOrgId}
          placeholder="UUID from seed or POST /v1/organizations"
        />
        <button type="button" onClick={() => void load()}>
          Fetch
        </button>
      </div>

      <AsyncState loading={loading} error={error} />

      {data ? (
        <>
          <StatusBanner tone="success">
            Loaded <strong>{data.name}</strong> ({data.public_id})
          </StatusBanner>
          <JsonDisplay value={data} title="API response" />
        </>
      ) : null}
    </div>
  );
}
