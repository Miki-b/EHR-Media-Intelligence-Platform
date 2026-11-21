// WCAG contrast check for the palette in src/index.css. Runs before every build
// (npm "prebuild"); exits non-zero if any text/background pair below is under AA.
//   node scripts/check-contrast.mjs
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
const tokens = Object.fromEntries(
  [...css.matchAll(/--color-([a-z-]+):\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()]),
);

function parse(value) {
  const hex = value.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return { rgb: [(n >> 16) & 255, (n >> 8) & 255, n & 255], alpha: 1 };
  }
  const rgb = value.match(/^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+))?\s*\)$/);
  if (rgb) return { rgb: rgb.slice(1, 4).map(Number), alpha: rgb[4] ? Number(rgb[4]) : 1 };
  throw new Error(`cannot parse colour ${value}`);
}

const WHITE = { rgb: [255, 255, 255], alpha: 1 };
const color = (name) => (name === "white" ? WHITE : parse(tokens[name]));

// A translucent tint as it actually renders: composited over the panel (white).
function solid(c, under = color("panel")) {
  return c.rgb.map((v, i) => v * c.alpha + under.rgb[i] * (1 - c.alpha));
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(fg, bg) {
  const [a, b] = [luminance(solid(color(fg))), luminance(solid(color(bg)))].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

const AA = 4.5;
// [foreground, background]: every pairing the UI renders as text.
const PAIRS = [
  ["primary", "panel"], ["primary", "paper"], ["primary-hover", "panel"],
  ["white", "primary"], ["white", "primary-hover"],
  ["primary", "primary-tint"], ["ink", "primary-tint"], ["muted", "primary-tint"],
  ["ink", "panel"], ["ink", "paper"], ["muted", "panel"], ["muted", "paper"],
  ["faint", "panel"], ["faint", "paper"],
  ["critical", "panel"], ["critical", "paper"],
  ["warning", "panel"], ["ink", "warning-tint"],
  ["ok", "panel"],
  ["doc", "panel"], ["report", "panel"], ["summary", "panel"],
  ["doc", "primary-tint"], ["report", "primary-tint"], ["summary", "primary-tint"],
];

let failed = 0;
for (const [fg, bg] of PAIRS) {
  const r = ratio(fg, bg);
  const ok = r >= AA;
  failed += ok ? 0 : 1;
  console.log(`${ok ? "pass" : "FAIL"}  ${r.toFixed(2).padStart(5)}:1  ${fg} on ${bg}`);
}
if (failed) {
  console.error(`\n${failed} pair(s) below WCAG AA ${AA}:1`);
  process.exit(1);
}
console.log(`\nall ${PAIRS.length} pairs meet WCAG AA (${AA}:1)`);
