import "server-only";

export function recruitingReleaseEnabled(): boolean {
  return process.env.CONNECTMD_RECRUITING_ENABLED === "true";
}
