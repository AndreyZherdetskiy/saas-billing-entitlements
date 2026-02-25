export interface RuntimeConfig {
  apiBaseUrl?: string;
  apiKey?: string;
  platformAdminApiKey?: string;
  demoOrgId?: string;
  demoFeatureKeys?: string;
}

declare global {
  interface Window {
    __RUNTIME_CONFIG__?: RuntimeConfig;
  }
}

function readRuntime(key: keyof RuntimeConfig): string | undefined {
  const runtime = window.__RUNTIME_CONFIG__?.[key];
  if (runtime && runtime.trim() !== "") {
    return runtime.trim();
  }
  return undefined;
}

function readVite(key: string): string | undefined {
  const value = import.meta.env[key];
  if (typeof value === "string" && value.trim() !== "") {
    return value.trim();
  }
  return undefined;
}

export function getApiBaseUrl(): string {
  return readRuntime("apiBaseUrl") ?? readVite("VITE_API_BASE_URL") ?? "";
}

export function getApiKey(): string {
  return readRuntime("apiKey") ?? readVite("VITE_API_KEY") ?? "";
}

/** Platform-admin routes; falls back to primary API key when unset (Gate C). */
export function getPlatformAdminApiKey(): string {
  return (
    readRuntime("platformAdminApiKey") ??
    readVite("VITE_PLATFORM_ADMIN_API_KEY") ??
    getApiKey()
  );
}

export function getDemoOrgId(): string {
  return readRuntime("demoOrgId") ?? readVite("VITE_DEMO_ORG_ID") ?? "";
}

export function getDemoFeatureKeys(): string[] {
  const raw =
    readRuntime("demoFeatureKeys") ??
    readVite("VITE_DEMO_FEATURE_KEYS") ??
    "api_calls,seats";
  return raw
    .split(",")
    .map((key) => key.trim())
    .filter((key) => key.length > 0);
}
