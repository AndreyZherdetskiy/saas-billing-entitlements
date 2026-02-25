import { useCallback, useEffect, useState } from "react";
import {
  fetchReconciliationDiscrepancies,
  fetchReconciliationRuns,
} from "../api/billing";
import { formatApiError } from "../api/errors";
import { AsyncState, JsonDisplay, StatusBanner } from "../components/Display";
import { PageHelp } from "../components/PageHelp";
import { getPlatformAdminApiKey } from "../config";
import type { ReconciliationDiscrepancy, ReconciliationRun } from "../types/api";

function formatCents(cents: number | null): string {
  if (cents === null) {
    return "—";
  }
  return `$${(cents / 100).toFixed(2)}`;
}

export function ReconciliationPage() {
  const [runs, setRuns] = useState<ReconciliationRun[] | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [discrepancies, setDiscrepancies] = useState<ReconciliationDiscrepancy[] | null>(
    null,
  );
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [loadingDiscrepancies, setLoadingDiscrepancies] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [discrepanciesError, setDiscrepanciesError] = useState<string | null>(null);

  const adminConfigured = Boolean(getPlatformAdminApiKey());

  const loadRuns = useCallback(async () => {
    if (!adminConfigured) {
      setRunsError(
        "platform_admin API key required (runtime config or VITE_PLATFORM_ADMIN_API_KEY)",
      );
      setRuns(null);
      return;
    }

    setLoadingRuns(true);
    setRunsError(null);
    try {
      const response = await fetchReconciliationRuns();
      setRuns(response);
      if (response.length > 0) {
        setSelectedRunId((current) => current ?? response[0].id);
      }
    } catch (err) {
      setRuns(null);
      setRunsError(formatApiError(err));
    } finally {
      setLoadingRuns(false);
    }
  }, [adminConfigured]);

  const loadDiscrepancies = useCallback(async (runId: string) => {
    setLoadingDiscrepancies(true);
    setDiscrepanciesError(null);
    try {
      const response = await fetchReconciliationDiscrepancies(runId);
      setDiscrepancies(response);
    } catch (err) {
      setDiscrepancies(null);
      setDiscrepanciesError(formatApiError(err));
    } finally {
      setLoadingDiscrepancies(false);
    }
  }, []);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    if (!selectedRunId) {
      setDiscrepancies(null);
      return;
    }
    void loadDiscrepancies(selectedRunId);
  }, [loadDiscrepancies, selectedRunId]);

  const selectedRun = runs?.find((run) => run.id === selectedRunId) ?? null;
  const mismatchCount =
    selectedRun && typeof selectedRun.stats.mismatch_count === "number"
      ? selectedRun.stats.mismatch_count
      : (discrepancies?.length ?? 0);

  return (
    <div className="page">
      <header className="page-header">
        <h2>Reconciliation runs</h2>
        <p>
          GET /v1/admin/reconciliation/runs and /runs/{"{id}"}/discrepancies — requires
          platform_admin key.
        </p>
      </header>

      <PageHelp>
        <p>
          Finance check: compare provider invoices vs internal ledger / invoices.
          Runs are created by ops (<code>POST /v1/admin/reconciliation/run</code>) or
          scheduled jobs — this screen only lists results.
        </p>
        <ul>
          <li>Select a run to load discrepancy rows (amount / missing invoice, etc.).</li>
          <li>
            Seeded recon-mismatch orgs (prod-like seed) are useful to demo non-empty
            discrepancy lists.
          </li>
          <li>
            For ops response on mismatches, follow the reconciliation mismatch
            runbook (triage discrepancies, verify provider vs ledger, escalate
            if unresolved).
          </li>
        </ul>
      </PageHelp>

      <div className="toolbar">
        <button type="button" onClick={() => void loadRuns()}>
          Refresh runs
        </button>
      </div>

      {!adminConfigured ? (
        <StatusBanner tone="warning">
          Set <code>DEMO_UI_PLATFORM_ADMIN_API_KEY</code> (or fallback API key) for admin
          reconciliation routes.
        </StatusBanner>
      ) : null}

      <AsyncState loading={loadingRuns} error={runsError} />

      {runs && runs.length > 0 ? (
        <section className="panel">
          <h2>Runs ({runs.length})</h2>
          <table className="data-table">
            <thead>
              <tr>
                <th>Select</th>
                <th>Started</th>
                <th>Type</th>
                <th>Status</th>
                <th>Stats</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <label className="checkbox">
                      <input
                        type="radio"
                        name="recon-run"
                        checked={selectedRunId === run.id}
                        onChange={() => setSelectedRunId(run.id)}
                      />
                    </label>
                  </td>
                  <td>{new Date(run.started_at).toLocaleString()}</td>
                  <td>{run.run_type}</td>
                  <td>{run.status}</td>
                  <td>
                    <code>{JSON.stringify(run.stats)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : runs && runs.length === 0 ? (
        <StatusBanner tone="info">
          No reconciliation runs yet. Trigger via{" "}
          <code>POST /v1/admin/reconciliation/run</code> (ops / seed script).
        </StatusBanner>
      ) : null}

      {selectedRun ? (
        <StatusBanner tone={mismatchCount > 0 ? "warning" : "success"}>
          Run <strong>{selectedRun.id.slice(0, 8)}…</strong> — status{" "}
          <strong>{selectedRun.status}</strong>, discrepancies:{" "}
          <strong>{mismatchCount}</strong>
        </StatusBanner>
      ) : null}

      <AsyncState loading={loadingDiscrepancies} error={discrepanciesError} />

      {discrepancies && discrepancies.length > 0 ? (
        <section className="panel">
          <h2>Discrepancies</h2>
          <table className="data-table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>External invoice</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Delta</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {discrepancies.map((row) => (
                <tr key={row.id}>
                  <td>{row.kind}</td>
                  <td>{row.external_invoice_id ?? "—"}</td>
                  <td>{formatCents(row.expected_amount_cents)}</td>
                  <td>{formatCents(row.actual_amount_cents)}</td>
                  <td>{formatCents(row.delta_cents)}</td>
                  <td>{new Date(row.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : discrepancies && discrepancies.length === 0 && selectedRunId ? (
        <StatusBanner tone="success">No discrepancies for selected run.</StatusBanner>
      ) : null}

      {selectedRun ? (
        <JsonDisplay
          value={{
            run: selectedRun,
            discrepancies,
          }}
          title="Raw API responses"
        />
      ) : null}
    </div>
  );
}
