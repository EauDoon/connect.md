export type DeploymentEnvironment = Readonly<Record<string, string | undefined>>;

type ResponseHeader = { key: string; value: string };

export function vercelSecurityHeaders(environment: DeploymentEnvironment = process.env): ResponseHeader[] {
  if (environment.VERCEL !== "1") return [];
  if (environment.NODE_ENV === "development") {
    throw new Error("Vercel security headers require a production Next.js build. Use npm run dev with VERCEL unset for local development.");
  }

  const production = environment.VERCEL_ENV === "production"
    || (!environment.VERCEL_ENV && environment.NODE_ENV === "production");
  validateSiteOrigin(environment.NEXT_PUBLIC_SITE_URL, production);

  const contentSecurityPolicy = [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "form-action 'self'",
    "script-src 'self'",
    "script-src-elem 'self' 'unsafe-inline'",
    "script-src-attr 'none'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "frame-src 'self' blob:",
    "manifest-src 'self'",
    "media-src 'none'",
  ].join("; ");

  const headers: ResponseHeader[] = [
    { key: "Content-Security-Policy", value: contentSecurityPolicy },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "SAMEORIGIN" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  ];
  if (production) headers.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
  return headers;
}

function validateSiteOrigin(value: string | undefined, required: boolean) {
  const raw = value?.trim();
  if (!raw) {
    if (required) throw new Error("NEXT_PUBLIC_SITE_URL must be an explicit HTTPS origin for Vercel production.");
    return;
  }

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("NEXT_PUBLIC_SITE_URL must be a canonical HTTPS origin.");
  }
  if (
    parsed.protocol !== "https:"
    || parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
  ) throw new Error("NEXT_PUBLIC_SITE_URL must be a canonical HTTPS origin.");
}
