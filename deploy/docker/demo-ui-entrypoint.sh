#!/bin/sh
set -eu

# Runtime injection — secrets are NOT baked into image layers (Gate C).
cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.__RUNTIME_CONFIG__ = {
  apiBaseUrl: "${DEMO_UI_API_BASE_URL:-}",
  apiKey: "${DEMO_UI_API_KEY:-}",
  platformAdminApiKey: "${DEMO_UI_PLATFORM_ADMIN_API_KEY:-}",
  demoOrgId: "${DEMO_UI_ORG_ID:-}",
  demoFeatureKeys: "${DEMO_UI_FEATURE_KEYS:-api_calls,seats}"
};
EOF
