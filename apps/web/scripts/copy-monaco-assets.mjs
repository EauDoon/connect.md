import { access, cp, mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = fileURLToPath(new URL("..", import.meta.url));
const source = join(appRoot, "node_modules", "monaco-editor", "min", "vs");
const target = process.argv[2]
  ? resolve(process.cwd(), process.argv[2])
  : join(appRoot, "public", "monaco", "vs");
const staging = `${target}.copy-${process.pid}`;
const inPlaceConfiguration = /\b([A-Za-z_$][\w$]*)=[A-Za-z_$][\w$]*\.IN_PLACE\|\|!1/g;

async function javascriptFiles(directory) {
  const paths = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      paths.push(...(await javascriptFiles(path)));
    } else if (entry.isFile() && entry.name.endsWith(".js")) {
      paths.push(path);
    }
  }
  return paths;
}

async function disableBundledDomPurifyInPlace(directory) {
  let replacements = 0;
  for (const path of await javascriptFiles(directory)) {
    const sourceText = await readFile(path, "utf8");
    const hardened = sourceText.replace(inPlaceConfiguration, (_match, configuredFlag) => {
      replacements += 1;
      return `${configuredFlag}=!1`;
    });
    if (hardened !== sourceText) {
      await writeFile(path, hardened, "utf8");
    }
  }
  if (replacements !== 1) {
    throw new Error("The pinned Monaco DOMPurify hardening anchor was not unique.");
  }
}

if (resolve(source) === target) {
  throw new Error("The Monaco asset target must differ from its node_modules source.");
}

await access(join(source, "loader.js"), constants.R_OK);
await rm(staging, { force: true, recursive: true });
await mkdir(dirname(target), { recursive: true });

try {
  await cp(source, staging, { recursive: true });
  await disableBundledDomPurifyInPlace(staging);
  await access(join(staging, "loader.js"), constants.R_OK);
  await rm(target, { force: true, recursive: true });
  await rename(staging, target);
} finally {
  await rm(staging, { force: true, recursive: true });
}

process.stdout.write(`Copied hardened Monaco 0.56.0 assets to ${target}\n`);
