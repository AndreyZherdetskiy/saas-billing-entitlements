import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { EntitlementsPage } from "./pages/EntitlementsPage";
import { DunningPage } from "./pages/DunningPage";
import { OrganizationPage } from "./pages/OrganizationPage";
import { ReconciliationPage } from "./pages/ReconciliationPage";
import { SubscriptionPage } from "./pages/SubscriptionPage";
import { UsagePage } from "./pages/UsagePage";
import { WebhookStatusPage } from "./pages/WebhookStatusPage";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<OrganizationPage />} />
          <Route path="subscription" element={<SubscriptionPage />} />
          <Route path="entitlements" element={<EntitlementsPage />} />
          <Route path="usage" element={<UsagePage />} />
          <Route path="reconciliation" element={<ReconciliationPage />} />
          <Route path="dunning" element={<DunningPage />} />
          <Route path="webhook-status" element={<WebhookStatusPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
