import { NavLink, Outlet } from "react-router-dom";
import { isCrossOriginApiBaseRisky } from "../api/errors";
import { StatusBanner } from "./Display";
import {
  getApiBaseUrl,
  getApiKey,
  getDemoOrgId,
  getPlatformAdminApiKey,
} from "../config";

const navItems = [
  { to: "/", label: "Organization", end: true },
  { to: "/subscription", label: "Subscription" },
  { to: "/entitlements", label: "Entitlements" },
  { to: "/usage", label: "Usage" },
  { to: "/reconciliation", label: "Reconciliation" },
  { to: "/dunning", label: "Dunning" },
  { to: "/webhook-status", label: "Webhook status" },
];

export function Layout() {
  const apiConfigured = Boolean(getApiKey());
  const adminConfigured = Boolean(getPlatformAdminApiKey());
  const orgConfigured = Boolean(getDemoOrgId());
  const crossOriginRisky = isCrossOriginApiBaseRisky();

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Billing Platform</p>
          <h1>Demo UI</h1>
          <p className="lede">
            Read-only operator console for the Internal/Admin API. Prefills come from
            seed (`DEMO_UI_ORG_ID` / API keys). Billing decisions stay on the server —
            this SPA only calls HTTP and renders JSON.
          </p>
        </div>
        <dl className="config-summary">
          <div>
            <dt>API base</dt>
            <dd>{getApiBaseUrl() || "(same origin / nginx proxy)"}</dd>
          </div>
          <div>
            <dt>API key</dt>
            <dd>{apiConfigured ? "configured" : "missing"}</dd>
          </div>
          <div>
            <dt>Admin key</dt>
            <dd>{adminConfigured ? "configured" : "missing"}</dd>
          </div>
          <div>
            <dt>Demo org</dt>
            <dd>{orgConfigured ? getDemoOrgId() : "set DEMO_UI_ORG_ID"}</dd>
          </div>
        </dl>
      </header>

      {crossOriginRisky ? (
        <StatusBanner tone="warning">
          <strong>API base is cross-origin</strong> ({getApiBaseUrl()}). The browser
          will likely show <em>Failed to fetch</em> because billing-api does not send
          CORS headers. For Docker demo-ui leave <code>DEMO_UI_API_BASE_URL</code>{" "}
          empty, recreate <code>demo-ui</code>, then retry — nginx proxies{" "}
          <code>/v1</code> and <code>/health</code> to the API on the same origin.
        </StatusBanner>
      ) : null}

      {!apiConfigured ? (
        <StatusBanner tone="warning">
          No API key in runtime config. Set <code>DEMO_UI_API_KEY</code> from{" "}
          <code>.env.example</code> (or root <code>.env</code> after{" "}
          <code>cp .env.example .env</code>) and recreate demo-ui.
        </StatusBanner>
      ) : null}

      <aside className="page-help layout-help">
        <p>
          <strong>How to use:</strong> open a screen → confirm the prefilled org id
          (from seed) → click Fetch/Evaluate. Default org in the field is only a
          convenience — the request still needs a valid Bearer key and a reachable
          API.
        </p>
        <ul>
          <li>
            <strong>Organization / Subscription / Entitlements</strong> — tenant
            API key (or platform_admin) + org <code>public_id</code>.
          </li>
          <li>
            <strong>Reconciliation / Dunning</strong> — platform_admin routes.
          </li>
          <li>
            <strong>Webhook status</strong> — health probes + subscription poll after
            mock-stripe events (not a full webhook inbox).
          </li>
        </ul>
      </aside>

      <nav className="app-nav">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
