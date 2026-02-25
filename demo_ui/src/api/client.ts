import { getApiBaseUrl, getApiKey } from "../config";
import type { ApiErrorBody } from "../types/api";
import { ApiError } from "../types/api";

interface RequestAuthOptions {
  apiKey?: string;
}

function buildUrl(path: string): string {
  const base = getApiBaseUrl().replace(/\/$/, "");
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${base}${normalized}`;
}

function authHeaders(options?: RequestAuthOptions): HeadersInit {
  const apiKey = options?.apiKey ?? getApiKey();
  if (!apiKey) {
    return {};
  }
  return { Authorization: `Bearer ${apiKey}` };
}

async function parseError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = await response.text().catch(() => null);
  }

  let message = `HTTP ${response.status}`;
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as ApiErrorBody).detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail) && detail.length > 0) {
      message = detail.map((item) => item.msg).join("; ");
    }
  }

  return new ApiError(response.status, body, message);
}

async function request(
  path: string,
  init: RequestInit,
): Promise<Response> {
  const url = buildUrl(path);
  try {
    return await fetch(url, init);
  } catch (err) {
    if (err instanceof TypeError) {
      const base = getApiBaseUrl() || "(same origin)";
      throw new TypeError(
        `Failed to fetch ${url} (API base ${base}). ` +
          `Leave DEMO_UI_API_BASE_URL empty in Docker so nginx proxies /v1 → billing-api; ` +
          `http://localhost:8000 from :8080 is cross-origin and fails without CORS.`,
      );
    }
    throw err;
  }
}

export async function apiGet<T>(path: string, options?: RequestAuthOptions): Promise<T> {
  const response = await request(path, {
    headers: {
      Accept: "application/json",
      ...authHeaders(options),
    },
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as T;
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  options?: RequestAuthOptions,
): Promise<T> {
  const response = await request(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...authHeaders(options),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as T;
}

export async function apiGetOptionalAuth<T>(path: string): Promise<T> {
  return apiGet<T>(path);
}
