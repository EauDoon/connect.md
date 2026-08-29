import { parsePublishableKey } from "@clerk/shared/keys";

export type DeploymentEnvironment = Readonly<Record<string, string | undefined>>;

type ResponseHeader = { key: string; value: string };

const CLERK_ACCOUNTS_ORIGIN = "https://*.clerk.accounts.dev";
const CLERK_IMAGE_ORIGIN = "https://img.clerk.com";
const CLOUDFLARE_CHALLENGE_ORIGIN = "https://challenges.cloudflare.com";
const CLERK_PROTECT_SCRIPT_ORIGIN = "https://*.protect.clerk.com";
const CLERK_PROTECT_CONNECT_ORIGIN = "https://*.protect.clerk.com:*";
const CLERK_PROTECT_FRAME_ORIGIN = "https://*.protect.clerk.com";

const CLERK_PUBLISHABLE_KEY_ERROR =
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a well-formed Clerk publishable key for Vercel.";

export function vercelSecurityHeaders(environment: DeploymentEnvironment = process.env): ResponseHeader[] {
  if (environment.VERCEL !== "1") return [];
  if (environment.NODE_ENV === "development") {
    throw new Error("Vercel security headers require a production Next.js build. Use npm run dev with VERCEL unset for local development.");
  }

  const vercelProduction = environment.VERCEL_ENV === "production"
    || (!environment.VERCEL_ENV && environment.NODE_ENV === "production");
  const siteOrigin = configuredOrigin(environment.NEXT_PUBLIC_SITE_URL, "NEXT_PUBLIC_SITE_URL", vercelProduction);
  const apiOrigin = configuredOrigin(environment.NEXT_PUBLIC_API_BASE_URL, "NEXT_PUBLIC_API_BASE_URL", vercelProduction);

  const configuredClerkOrigins = clerkSecurityOrigins(environment, vercelProduction, siteOrigin);
  const clerkEnabled = configuredClerkOrigins.length > 0;
  const clerkSources = clerkEnabled ? [CLERK_ACCOUNTS_ORIGIN, ...configuredClerkOrigins] : [];
  const scriptSources = [
    "'self'",
    ...clerkSources,
    ...(clerkEnabled ? [CLERK_PROTECT_SCRIPT_ORIGIN, CLOUDFLARE_CHALLENGE_ORIGIN] : []),
  ];
  const scriptElementSources = [
    "'self'",
    "'unsafe-inline'",
    ...clerkSources,
    ...(clerkEnabled ? [CLERK_PROTECT_SCRIPT_ORIGIN, CLOUDFLARE_CHALLENGE_ORIGIN] : []),
  ];
  const connectSources = [
    "'self'",
    apiOrigin,
    ...clerkSources,
    ...(clerkEnabled ? [CLERK_PROTECT_CONNECT_ORIGIN, CLERK_IMAGE_ORIGIN] : []),
  ].filter((origin): origin is string => Boolean(origin));
  const imageSources = ["'self'", "data:", "blob:", ...(clerkEnabled ? [CLERK_IMAGE_ORIGIN] : [])];
  const frameSources = [
    "'self'",
    "blob:",
    ...(clerkEnabled ? [CLOUDFLARE_CHALLENGE_ORIGIN, CLERK_PROTECT_FRAME_ORIGIN] : []),
  ];
  const contentSecurityPolicy = [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "form-action 'self'",
    `script-src ${scriptSources.join(" ")}`,
    `script-src-elem ${scriptElementSources.join(" ")}`,
    "script-src-attr 'none'",
    "style-src 'self' 'unsafe-inline'",
    `img-src ${imageSources.join(" ")}`,
    "font-src 'self' data:",
    `connect-src ${connectSources.join(" ")}`,
    "worker-src 'self' blob:",
    `frame-src ${frameSources.join(" ")}`,
    "manifest-src 'self'",
    "media-src 'none'"
  ].join("; ");

  const headers: ResponseHeader[] = [
    { key: "Content-Security-Policy", value: contentSecurityPolicy },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "SAMEORIGIN" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" }
  ];

  if (vercelProduction) headers.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
  return headers;
}

function clerkSecurityOrigins(environment: DeploymentEnvironment, requireHttps: boolean, siteOrigin: string | null): string[] {
  const publishableKey = environment.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim();
  if (!publishableKey) return [];

  const parsedKey = parsePublishableKey(publishableKey);
  if (!parsedKey) throw new Error(CLERK_PUBLISHABLE_KEY_ERROR);

  const satellite = configuredBoolean(
    environment.NEXT_PUBLIC_CLERK_IS_SATELLITE,
    "NEXT_PUBLIC_CLERK_IS_SATELLITE",
  );
  const domain = configuredHostname(environment.NEXT_PUBLIC_CLERK_DOMAIN, "NEXT_PUBLIC_CLERK_DOMAIN");
  const proxyOrigin = configuredProxyOrigin(
    environment.NEXT_PUBLIC_CLERK_PROXY_URL,
    "NEXT_PUBLIC_CLERK_PROXY_URL",
    requireHttps,
  );

  const hasProxy = Boolean(environment.NEXT_PUBLIC_CLERK_PROXY_URL?.trim());
  if (domain && hasProxy) {
    throw new Error("NEXT_PUBLIC_CLERK_DOMAIN and NEXT_PUBLIC_CLERK_PROXY_URL are mutually exclusive for Vercel.");
  }
  if (hasProxy && parsedKey.instanceType !== "production") {
    throw new Error("NEXT_PUBLIC_CLERK_PROXY_URL requires a production Clerk publishable key for Vercel.");
  }
  if (proxyOrigin && proxyOrigin !== siteOrigin) {
    throw new Error("NEXT_PUBLIC_CLERK_PROXY_URL must use the same origin as NEXT_PUBLIC_SITE_URL for Vercel.");
  }

  if (domain && !satellite) {
    throw new Error(
      "NEXT_PUBLIC_CLERK_DOMAIN requires NEXT_PUBLIC_CLERK_IS_SATELLITE=true for Vercel.",
    );
  }
  if (satellite && !domain && !hasProxy) {
    throw new Error(
      "NEXT_PUBLIC_CLERK_IS_SATELLITE=true requires NEXT_PUBLIC_CLERK_DOMAIN or NEXT_PUBLIC_CLERK_PROXY_URL for Vercel.",
    );
  }
  if (domain && parsedKey.instanceType !== "production") {
    throw new Error(
      "NEXT_PUBLIC_CLERK_DOMAIN is supported only with a production Clerk publishable key for Vercel.",
    );
  }

  const origins = [clerkFrontendApiOrigin(parsedKey.frontendApi)];
  if (domain) {
    const satelliteKey = parsePublishableKey(publishableKey, {
      domain,
      isSatellite: true,
    });
    if (!satelliteKey) throw new Error(CLERK_PUBLISHABLE_KEY_ERROR);
    origins.push(clerkFrontendApiOrigin(satelliteKey.frontendApi));
  }
  if (proxyOrigin) origins.push(proxyOrigin);
  return [...new Set(origins)];
}

function clerkFrontendApiOrigin(frontendApi: string | undefined): string {
  if (!frontendApi) throw new Error(CLERK_PUBLISHABLE_KEY_ERROR);
  let parsed: URL;
  try {
    parsed = new URL(`https://${frontendApi}`);
  } catch {
    throw new Error(CLERK_PUBLISHABLE_KEY_ERROR);
  }
  if (
    parsed.protocol !== "https:"
    || parsed.username
    || parsed.password
    || parsed.port
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || !isDnsHostname(parsed.hostname)
  ) {
    throw new Error(CLERK_PUBLISHABLE_KEY_ERROR);
  }
  return parsed.origin;
}

function configuredBoolean(value: string | undefined, variableName: string): boolean {
  const raw = value?.trim();
  if (!raw || raw === "false") return false;
  if (raw === "true") return true;
  throw new Error(`${variableName} must be "true" or "false" for Vercel.`);
}

function configuredHostname(value: string | undefined, variableName: string): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  if (!isDnsHostname(raw)) {
    throw new Error(`${variableName} must be a lowercase DNS hostname for Vercel.`);
  }
  return raw;
}

function isDnsHostname(value: string): boolean {
  if (value.length === 0 || value.length > 253 || value !== value.toLowerCase()) return false;
  const labels = value.split(".");
  if (labels.length < 2) return false;
  return labels.every((label) =>
    label.length > 0
    && label.length <= 63
    && !label.startsWith("-")
    && !label.endsWith("-")
    && /^[a-z0-9-]+$/u.test(label),
  );
}

function configuredProxyOrigin(
  value: string | undefined,
  variableName: string,
  requireHttps: boolean,
): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  if (raw.startsWith("/")) {
    if (
      raw.startsWith("//")
      || raw.includes("\\")
      || raw.includes("?")
      || raw.includes("#")
      || raw.split("/").some((part) => part === "." || part === "..")
    ) {
      throw new Error(`${variableName} must be a relative path or canonical HTTPS origin for Vercel.`);
    }
    return null;
  }

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`${variableName} must be a relative path or canonical HTTPS origin for Vercel.`);
  }
  if (
    (parsed.protocol !== "https:" && parsed.protocol !== "http:")
    || (requireHttps && parsed.protocol !== "https:")
    || parsed.username
    || parsed.password
    || parsed.pathname === ""
    || parsed.search
    || parsed.hash
    || !isDnsHostname(parsed.hostname)
  ) {
    throw new Error(`${variableName} must be a relative path or canonical${requireHttps ? " HTTPS" : ""} origin for Vercel.`);
  }
  return parsed.origin;
}

function configuredOrigin(value: string | undefined, variableName: string, requireHttps: boolean): string | null {
  const raw = value?.trim();
  if (!raw) {
    if (requireHttps) throw new Error(`${variableName} must be an explicit HTTPS origin for Vercel production.`);
    return null;
  }

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`${variableName} must be a canonical origin.`);
  }
  if ((parsed.protocol !== "https:" && parsed.protocol !== "http:")
    || (requireHttps && parsed.protocol !== "https:")
    || parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash) {
    throw new Error(`${variableName} must be a canonical${requireHttps ? " HTTPS" : ""} origin.`);
  }
  return parsed.origin;
}
