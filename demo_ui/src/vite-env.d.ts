/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_API_KEY?: string;
  readonly VITE_PLATFORM_ADMIN_API_KEY?: string;
  readonly VITE_DEMO_ORG_ID?: string;
  readonly VITE_DEMO_FEATURE_KEYS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
