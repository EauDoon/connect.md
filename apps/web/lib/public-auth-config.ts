export function publicAuthConfigured(publishableKey: string | undefined): publishableKey is string {
  return Boolean(publishableKey?.trim());
}
