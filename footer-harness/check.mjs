/* Compare glyph-affecting computed styles + sub-pixel positions, live vs local. */
import { chromium } from "playwright";

const LIVE = "https://hydradb.com/";
const LOCAL = "http://localhost:8799/footer-harness/harness.html";
const TARGETS = ["Why HydraDB", "Home", "Customer Support", "Build AI with compounding intelligence"];

const probe = (labels) => {
  const pick = (t) => {
    const els = [...document.querySelectorAll("a, p, h2")];
    return els.find((e) => (e.textContent || "").trim() === t);
  };
  const out = {};
  for (const t of labels) {
    const el = pick(t);
    if (!el) { out[t] = null; continue; }
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    out[t] = {
      x: +r.left.toFixed(2), y: +r.top.toFixed(2), w: +r.width.toFixed(2),
      ff: cs.fontFamily.split(",")[0], fs: cs.fontSize, fw: cs.fontWeight,
      lh: cs.lineHeight, ls: cs.letterSpacing, tt: cs.textTransform,
      tr: cs.textRendering, kern: cs.fontKerning,
      feat: cs.fontFeatureSettings, ffs: cs.fontVariationSettings,
      lig: cs.fontVariantLigatures, smooth: cs.webkitFontSmoothing,
    };
  }
  return out;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });

const pL = await ctx.newPage();
await pL.goto(LIVE, { waitUntil: "domcontentloaded", timeout: 90000 });
await pL.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await pL.waitForTimeout(2000);
const live = await pL.evaluate(probe, TARGETS);

const pM = await ctx.newPage();
await pM.goto(LOCAL, { waitUntil: "networkidle", timeout: 30000 });
await pM.waitForTimeout(400);
const local = await pM.evaluate(probe, TARGETS);

for (const t of TARGETS) {
  console.log(`\n### ${t}`);
  console.log("live :", JSON.stringify(live[t]));
  console.log("local:", JSON.stringify(local[t]));
}
await browser.close();
