import { readFileSync, readdirSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const globals = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
const minimumMutedTextOpacity = 0.8;

function srgbChannel(value: number): number {
  const channel = value / 255;
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

function luminance([red, green, blue]: readonly number[]): number {
  return 0.2126 * srgbChannel(red) + 0.7152 * srgbChannel(green) + 0.0722 * srgbChannel(blue);
}

function composite(foreground: readonly number[], background: readonly number[], opacity: number): [number, number, number] {
  return foreground.map((channel, index) => channel * opacity + background[index] * (1 - opacity)) as [number, number, number];
}

function contrastRatio(first: readonly number[], second: readonly number[]): number {
  const lighter = Math.max(luminance(first), luminance(second));
  const darker = Math.min(luminance(first), luminance(second));
  return (lighter + 0.05) / (darker + 0.05);
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return [".ts", ".tsx"].includes(extname(entry.name)) ? [path] : [];
  });
}

describe("muted text contrast contract", () => {
  it("raises every deliberately dim muted-text utility to the shared AA floor", () => {
    for (const opacity of [55, 60, 70, 75]) {
      expect(globals).toContain(`.text-mist\\/${opacity}`);
    }
    for (const opacity of [45, 55]) {
      expect(globals).toContain(`.placeholder\\:text-mist\\/${opacity}::placeholder`);
    }
    expect(globals.match(/--tw-text-opacity:\s*\.8;/gu)).toHaveLength(2);
    expect(globals).toMatch(/\.placeholder\\:text-mist\\\/45::placeholder,[\s\S]*?opacity:\s*1;/u);
  });

  it("keeps the composited mist color above WCAG AA on the lightest dark design surface", () => {
    const mist = [0xae, 0xb5, 0xc2] as const;
    const lightestDarkSurface = [0x29, 0x2d, 0x35] as const;
    const renderedMist = composite(mist, lightestDarkSurface, minimumMutedTextOpacity);
    expect(contrastRatio(renderedMist, lightestDarkSurface)).toBeGreaterThanOrEqual(4.5);
  });

  it("rejects new unremediated mist text or placeholder opacity utilities below the AA floor", () => {
    const sources = ["app", "components", "lib"].flatMap((directory) => sourceFiles(fileURLToPath(new URL(`../${directory}`, import.meta.url))));
    const permitted = new Set(["text-mist/55", "text-mist/60", "text-mist/70", "text-mist/75", "placeholder:text-mist/45", "placeholder:text-mist/55"]);
    const lowOpacityUtilities = sources.flatMap((path) => {
      const source = readFileSync(path, "utf8");
      return [...source.matchAll(/(?:placeholder:)?text-mist\/(\d+)/gu)]
        .map((match) => match[0])
        .filter((utility) => Number(utility.split("/")[1]) < minimumMutedTextOpacity * 100);
    });
    expect(new Set(lowOpacityUtilities)).toEqual(permitted);
  });
});
