/*
 * Structural probe: dumps the live Framer footer's layout at each breakpoint so
 * the port can match exact geometry (card width, stack direction, grid, video box,
 * social layout). Prints an indented tree of named frames with rects + flex info.
 */
import { chromium } from "playwright";

const WIDTHS = process.argv.slice(2).map(Number).filter(Boolean);
const LIST = WIDTHS.length ? WIDTHS : [1920, 1440, 1200, 810, 390];
const LIVE = "https://hydradb.com/";

const walkFn = () => {
  const cw = document.documentElement.clientWidth;
  const cands = [...document.querySelectorAll("div")].filter((d) => {
    const r = d.getBoundingClientRect();
    return Math.abs(r.width - cw) < 3 && r.height > 200 &&
      /AGI Context/.test(d.textContent || "") &&
      d.querySelector('[style*="repeating-linear-gradient"]');
  });
  cands.sort((a, b) => a.getBoundingClientRect().height - b.getBoundingClientRect().height);
  const band = cands[0];
  if (!band) return "NO BAND";
  const br = band.getBoundingClientRect();
  const ox = br.left, oy = br.top;
  const lines = [];
  let n = 0;
  const rel = (el) => {
    const r = el.getBoundingClientRect();
    return `${Math.round(r.left - ox)},${Math.round(r.top - oy)} ${Math.round(r.width)}x${Math.round(r.height)}`;
  };
  const walk = (el, d) => {
    if (n++ > 130) return;
    const cs = getComputedStyle(el);
    if (cs.display === "none") return;
    const nm = el.getAttribute && el.getAttribute("data-framer-name");
    const isTxt = el.classList && el.classList.contains("framer-text");
    const isMedia = ["IMG", "VIDEO", "A"].includes(el.tagName);
    if (nm || isTxt || isMedia) {
      let s = "  ".repeat(d) + (nm ? `[${nm}]` : el.tagName.toLowerCase()) + " " + rel(el);
      if (cs.display.includes("flex")) s += ` fx:${cs.flexDirection}/${cs.justifyContent}/${cs.alignItems}/g${cs.gap}`;
      if (cs.display.includes("grid")) s += ` grid:${cs.gridTemplateColumns}`;
      const pad = [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft].map((x) => parseInt(x) || 0);
      if (pad.some((x) => x)) s += ` p:${pad.join(",")}`;
      if (el.tagName === "A") s += ` "${(el.textContent || "").trim().slice(0, 14)}"`;
      if (isTxt) s += ` T${cs.fontSize}`;
      lines.push(s);
    }
    [...el.children].forEach((k) => walk(k, (nm || isTxt || isMedia) ? d + 1 : d));
  };
  walk(band, 0);
  return `BAND ${Math.round(br.width)}x${Math.round(br.height)}\n` + lines.join("\n");
};

const browser = await chromium.launch();
for (const width of LIST) {
  const ctx = await browser.newContext({ viewport: { width, height: 1000 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.goto(LIVE, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(2000);
  const out = await page.evaluate(walkFn);
  console.log(`\n================= ${width}px =================`);
  console.log(out);
  await ctx.close();
}
await browser.close();
