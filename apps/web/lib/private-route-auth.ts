export function privateRouteAuthConfigured(
  publishableKey: string | undefined,
  secretKey: string | undefined,
): boolean {
  return Boolean(publishableKey?.trim() && secretKey?.trim());
}
