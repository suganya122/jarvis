// WCAG AA for the design tokens, in both themes: 4.5:1 for text, 3:1 for focus rings and borders.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Read as plain text (Vitest blanks CSS imports). Tests run from the frontend folder.
const css = readFileSync("src/styles/tokens.css", "utf8");

function block(selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT_PAIRS: [string, string][] = [
  ...["text", "text-2", "text-3", "muted", "accent", "warn", "danger"].flatMap((fg): [string, string][] =>
    ["bg", "surface", "surface-2"].map((bg): [string, string] => [fg, bg]),
  ),
  ["accent-text", "accent"],
  ["danger-text", "danger"],
  ["warn-text", "warn-bg"],
  ["text", "warn-bg"],
  ["muted", "warn-bg"],
  ["accent", "accent-soft"],
  ["text", "user-bubble"],
];

describe.each([
  ["dark", block(":root {")],
  ["light", block(':root[data-theme="light"]')],
])("%s theme", (_name, tokens) => {
  it.each(TEXT_PAIRS)("%s on %s is at least 4.5:1", (fg, bg) => {
    expect(tokens[fg], fg).toBeDefined();
    expect(contrast(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it("focus ring and warning border are visible (3:1)", () => {
    expect(contrast(tokens.focus, tokens.bg)).toBeGreaterThanOrEqual(3);
    expect(contrast(tokens.focus, tokens.surface)).toBeGreaterThanOrEqual(3);
    expect(contrast(tokens.warn, tokens["warn-bg"])).toBeGreaterThanOrEqual(3);
  });
});
