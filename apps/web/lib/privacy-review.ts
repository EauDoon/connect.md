import { PROFILE_RESUME_MAX_UTF8_BYTES } from "@/lib/markdown";

export type PrivacyFinding = { code: string; line: number; message: string };
/** Heuristics only. Never return the matching value or fetch any linked resource. */
export function reviewPrivacy(markdown: string) {
  if (new TextEncoder().encode(markdown).length > PROFILE_RESUME_MAX_UTF8_BYTES) return { findings: [] as PrivacyFinding[], limited: true };
  const findings: PrivacyFinding[] = [];
  const lines = markdown.split("\n");
  const add = (code: string, line: number, message: string) => { if (findings.length < 20) findings.push({ code, line, message }); };
  let total = 0;
  for (const [index, text] of lines.entries()) {
    const found: Array<[string, string]> = [];
    const contactText = text.replace(/https?:\/\/[^\s)\]>"']+/giu, "");
    if (/[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}/iu.test(contactText)) found.push(["email", "An email address may be included. Confirm that you intend to share it."]);
    if (/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/u.test(text) || /\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*["']?\S{8,}/iu.test(text)) found.push(["credential", "Possible credential material. Inspect this line and remove any secret before sharing."]);
    if (/(?:file:\/\/|\b[A-Z]:\\|\/(?:Users|home)\/)/iu.test(text)) found.push(["local-path", "A local file path may reveal device or folder details and will not be portable."]);
    if (/!\[[^\]\n]*\]\(/u.test(text)) found.push(["image", "A Markdown image is blocked in this preview but another viewer may load its address."]);
    const urls = text.match(/https?:\/\/[^\s)\]>"']+/giu) ?? [];
    let unsafeUrl = false;
    let insecureUrl = false;
    for (const value of urls) {
      try {
        const url = new URL(value);
        unsafeUrl ||= Boolean(url.username || url.password) || [...url.searchParams.keys()].some((key) => /^(?:token|key|api_key|access_token|secret|password|signature|sig)$/iu.test(key));
        insecureUrl ||= url.protocol === "http:";
      } catch { /* Invalid links are left to the author's review. */ }
    }
    if (unsafeUrl) found.push(["url-secret", "A link contains credentials or a sensitive query parameter. Review it before sharing."]);
    if (insecureUrl) found.push(["http", "An unencrypted HTTP link is present. Check whether an HTTPS address is available."]);
    total += found.length;
    for (const [code, message] of found) add(code, index + 1, message);
  }
  return { findings, limited: total > findings.length };
}
