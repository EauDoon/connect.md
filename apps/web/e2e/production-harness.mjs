import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

import {
  classifyMonacoAssets,
  main,
  runChildCleanupProbe,
  runListenCleanupProbe,
  strictHttpOrigin,
} from "./production-runtime.mjs";

export {
  loadFixtures,
  validateDocumentFixture,
  validateEmptySearchFixture,
  validateFixturePayload,
  validateFixturePrivacy,
  validatePostFixture,
  validatePublicDocumentsFixture,
  validatePublicPostInventoryFixture,
  validateSearchFixture,
  validateSearchUnavailableFixture,
} from "./fixture-contracts.mjs";
export {
  summarizePlaywrightResult,
  validatePlaywrightJsonReceipt,
  waitForChildOutput,
} from "./production-runtime.mjs";

const isDirectExecution = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectExecution) {
  const originProbe = process.argv.indexOf("--validate-origin");
  const cleanupProbe = process.argv.indexOf("--probe-listen-cleanup");
  const childCleanupProbe = process.argv.indexOf("--probe-child-cleanup");
  const classificationProbe = process.argv.indexOf("--classify-monaco");
  if (originProbe >= 0) {
    try {
      process.stdout.write(`${strictHttpOrigin(process.argv[originProbe + 1] ?? "", "probe")}\n`);
    } catch {
      process.stderr.write("invalid E2E origin\n");
      process.exitCode = 2;
    }
  } else if (cleanupProbe >= 0) {
    runListenCleanupProbe().then(
      () => process.stdout.write("listen-cleanup-ok\n"),
      () => {
        process.stderr.write("listen cleanup probe failed\n");
        process.exitCode = 1;
      },
    );
  } else if (childCleanupProbe >= 0) {
    runChildCleanupProbe().then(
      () => process.stdout.write("child-cleanup-ok\n"),
      () => {
        process.stderr.write("child cleanup probe failed\n");
        process.exitCode = 1;
      },
    );
  } else if (classificationProbe >= 0) {
    try {
      const directoryExists = process.argv[classificationProbe + 1] === "true";
      const loaderExists = process.argv[classificationProbe + 2] === "true";
      process.stdout.write(`${classifyMonacoAssets(directoryExists, loaderExists)}\n`);
    } catch {
      process.stderr.write("invalid Monaco asset ownership state\n");
      process.exitCode = 2;
    }
  } else {
    main().catch(() => {
      process.stderr.write("production browser release gate failed\n");
      process.exitCode = 1;
    });
  }
}
