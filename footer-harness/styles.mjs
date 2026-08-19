/*
 * Computed-style node diff: compares the type/color of every footer text node
 * between the live Framer footer and the local footer, keyed by text content
 * (the DOM structures differ, so text is the stable join key). Catches
 * regressions a screenshot diff can miss. Box-model spacing is validated by the
 * pixel harness (diff.mjs).
 *
 * Prereq: static server on :8799 (repo root). Usage: node styles.mjs [width]
 */
import { chromium } from "playwright";

const WIDTH = Number(process.argv[2]) || 1440;
const LIVE = process.env.LIVE_URL || "https://hydradb.com/";
const LOCAL = (process.env.LOCAL_ORIGIN || "http://localhost:8799") + "/footer-harness/harness.html";
const PROPS = ["fontFamily", "fontSize", "lineHeight", "letterSpacing", "color"];

const collect = (isLive) => {
  let root;
  if (isLive) {
    const cw = document.documentElement.clientWidth;
    root = [...document.querySelectorAll("div")]
      .filter((d) => {
        const r = d.getBoundingClientRect();
        return Math.abs(r.width - cw) < 3 && r.height > 200 &&
          /AGI Context/.test(d.textContent || "") &&
          d.querySelector('[style*="repeating-linear-gradient"]');
      })
      .sort((a, b) => a.getBoundingClientRect().height - b.getBoundingClientRect().height)[0];
  } else {
    root = document.querySelector(".hdb-footer");
  }
  if (!root) return {};
  const out = {};
  root.querySelectorAll("*").forEach((el) => {
    const direct = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
    if (!direct || direct.length > 40 || out[direct]) return;
    const cs = getComputedStyle(el);
    out[direct] = {
      fontFamily: cs.fontFamily.split(",")[0].replace(/["']/g, ""),
      fontSize: cs.fontSize,
      lineHeight: cs.lineHeight,
      letterSpacing: cs.letterSpacing,
      color: cs.color,
    };
  });
  return out;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: WIDTH, height: 1200 }, deviceScaleFactor: 1 });

const pL = await ctx.newPage();
await pL.goto(LIVE, { waitUntil: "domcontentloaded", timeout: 90000 });
await pL.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await pL.waitForTimeout(2500);
await pL.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
const live = await pL.evaluate(collect, true);

const pM = await ctx.newPage();
await pM.goto(LOCAL, { waitUntil: "networkidle", timeout: 30000 });
await pM.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
await pM.waitForTimeout(400);
const local = await pM.evaluate(collect, false);
await browser.close();

const keys = Object.keys(live).filter((k) => local[k]);
let fails = 0, checked = 0;
console.log(`\n=== computed-style diff @ ${WIDTH}px (${keys.length} shared text nodes) ===`);
for (const k of keys) {
  const diffs = PROPS.filter((p) => live[k][p] !== local[k][p]);
  checked += PROPS.length;
  if (diffs.length) {
    fails += diffs.length;
    console.log(`✗ "${k}"`);
    for (const p of diffs) console.log(`    ${p}: live=${live[k][p]}  local=${local[k][p]}`);
  }
}
const onlyLive = Object.keys(live).filter((k) => !local[k]);
const onlyLocal = Object.keys(local).filter((k) => !live[k]);
if (onlyLive.length) console.log("nodes only in live :", onlyLive.join(" | "));
if (onlyLocal.length) console.log("nodes only in local:", onlyLocal.join(" | "));
console.log(`\n${fails === 0 ? "PASS" : "FAIL"} — ${checked - fails}/${checked} properties match across ${keys.length} nodes`);
process.exit(fails === 0 ? 0 : 1);
