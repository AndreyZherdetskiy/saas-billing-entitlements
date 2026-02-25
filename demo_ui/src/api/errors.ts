import { ApiError } from "../types/api";
import { getApiBaseUrl } from "../config";

/** Human-readable errors for demo UI (especially browser "Failed to fetch" / CORS). */
export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) {
    return `${err.status}: ${err.message}`;
  }
  if (err instanceof TypeError) {
    const base = getApiBaseUrl() || "(same origin)";
    return (
      `Network error (${err.message}). API base: ${base}. ` +
      `In Docker demo-ui leave DEMO_UI_API_BASE_URL empty so nginx proxies /v1 and /health ` +
      `to billing-api (same origin). Setting http://localhost:8000 forces a cross-origin ` +
      `browser call; the API has no CORS headers, so the browser reports Failed to fetch ` +
      `even when the org id and API key are correct.`
    );
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "Request failed";
}

/** True when runtime base URL likely triggers browser CORS against local API. */
export function isCrossOriginApiBaseRisky(): boolean {
  const base = getApiBaseUrl();
  if (!base) {
    return false;
  }
  try {
    const api = new URL(base, window.location.origin);
    return api.origin !== window.location.origin;
  } catch {
    return false;
  }
}
