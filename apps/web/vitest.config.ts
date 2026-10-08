import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) }
  },
  // 15s instead of the 5s default: cold imports of the larger suites (for
  // example dual-mode-continuity) time out under CPU contention on shared CI
  // runners and busy workstations, though each passes alone in well under 5s.
  test: { environment: "node", include: ["tests/**/*.test.ts"], testTimeout: 15_000 }
});
