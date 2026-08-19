/*
 * Pixel-diff harness: local HydraDB footer vs the live Framer footer.
 *
 * Captures the footer band from https://hydradb.com and from the local harness
 * page at 5 Framer breakpoints, masks the (frame-unsynced) video region in both,
 * and reports the percentage of matching pixels. Screenshots + diffs land in ./out.
 *
 * Prereq: a static server serving the repo root at LOCAL_ORIGIN (default :8799).
 *   python3 -m http.server 8799   (run from the repo root)
 */
import { chromium } from "playwright";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, "out");
fs.mkdirSync(OUT, { recursive: true });

const WIDTHS = [1920, 1440, 1200, 810, 390];
const LIVE = process.env.LIVE_URL || "https://hydradb.com/";
const LOCAL_ORIGIN = process.env.LOCAL_ORIGIN || "http://localhost:8799";
const LOCAL = `${LOCAL_ORIGIN}/footer-harness/harness.html`;
const THRESHOLD = 0.1; // pixelmatch per-pixel color sensitivity (AA-tolerant)

async function grabLive(page) {
  await page.goto(LIVE, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(2500);
  await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
  const info = await page.evaluate(() => {
    const cw = document.documentElement.clientWidth;
    const cands = [...document.querySelectorAll("div")].filter((d) => {
      const r = d.getBoundingClientRect();
      return Math.abs(r.width - cw) < 3 && r.height > 200 &&
        /AGI Context/.test(d.textContent || "") &&
        d.querySelector('[style*="repeating-linear-gradient"]');
    });
    cands.sort((a, b) => a.getBoundingClientRect().height - b.getBoundingClientRect().height);
    const band = cands[0];
    if (!band) return null;
    band.setAttribute("data-hdb-band", "1");
    // Hide fixed/sticky page chrome (sticky nav, chat widget) so tall element
    // captures aren't polluted by overlays the footer doesn't have.
    [...document.querySelectorAll("body *")].forEach((el) => {
      if (band.contains(el) || el.contains(band)) return;
      const p = getComputedStyle(el).position;
      if (p === "fixed" || p === "sticky") el.style.setProperty("display", "none", "important");
    });
    const br = band.getBoundingClientRect();
    const videos = [...band.querySelectorAll("video")].map((v) => {
      try { v.pause(); v.currentTime = 0; } catch (e) {}
      const r = v.getBoundingClientRect();
      return { x: r.left - br.left, y: r.top - br.top, w: r.width, h: r.height };
    });
    return { videos, w: Math.round(br.width), h: Math.round(br.height) };
  });
  if (!info) throw new Error("live footer band not found");
  await page.waitForTimeout(300);
  const buf = await page.locator('[data-hdb-band="1"]').screenshot();
  return { buf, ...info };
}

async function grabLocal(page) {
  await page.goto(LOCAL, { waitUntil: "networkidle", timeout: 30000 });
  await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
  await page.waitForTimeout(500);
  const info = await page.evaluate(() => {
    const band = document.querySelector(".hdb-footer");
    if (!band) return null;
    const br = band.getBoundingClientRect();
    const videos = [...band.querySelectorAll("video")].map((v) => {
      try { v.pause(); v.currentTime = 0; } catch (e) {}
      const r = v.getBoundingClientRect();
      return { x: r.left - br.left, y: r.top - br.top, w: r.width, h: r.height };
    });
    return { videos, w: Math.round(br.width), h: Math.round(br.height) };
  });
  if (!info) throw new Error("local footer not found (footer.js did not mount)");
  await page.waitForTimeout(150);
  const buf = await page.locator(".hdb-footer").screenshot();
  return { buf, ...info };
}

function crop(png, W, H) {
  if (png.width === W && png.height === H) return png;
  const out = new PNG({ width: W, height: H });
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      const s = (png.width * y + x) << 2;
      const d = (W * y + x) << 2;
      out.data[d] = png.data[s]; out.data[d + 1] = png.data[s + 1];
      out.data[d + 2] = png.data[s + 2]; out.data[d + 3] = png.data[s + 3];
    }
  return out;
}

function maskRect(png, r, pad = 2) {
  const x0 = Math.max(0, Math.floor(r.x - pad)), y0 = Math.max(0, Math.floor(r.y - pad));
  const x1 = Math.min(png.width, Math.ceil(r.x + r.w + pad)), y1 = Math.min(png.height, Math.ceil(r.y + r.h + pad));
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const i = (png.width * y + x) << 2;
      png.data[i] = 0; png.data[i + 1] = 0; png.data[i + 2] = 0; png.data[i + 3] = 255;
    }
}

async function run() {
  const browser = await chromium.launch();
  const rows = [];
  for (const width of WIDTHS) {
    const ctx = await browser.newContext({ viewport: { width, height: 1200 }, deviceScaleFactor: 1 });
    try {
      const live = await grabLive(await ctx.newPage());
      const local = await grabLocal(await ctx.newPage());
      fs.writeFileSync(path.join(OUT, `live-${width}.png`), live.buf);
      fs.writeFileSync(path.join(OUT, `local-${width}.png`), local.buf);

      let a = PNG.sync.read(live.buf);
      let b = PNG.sync.read(local.buf);
      const W = Math.min(a.width, b.width), H = Math.min(a.height, b.height);
      a = crop(a, W, H); b = crop(b, W, H);
      for (const v of [...live.videos, ...local.videos]) { maskRect(a, v); maskRect(b, v); }

      const diff = new PNG({ width: W, height: H });
      const mismatched = pixelmatch(a.data, b.data, diff.data, W, H, { threshold: THRESHOLD });
      fs.writeFileSync(path.join(OUT, `diff-${width}.png`), PNG.sync.write(diff));
      const total = W * H;
      rows.push({ width, W, H, liveWH: `${live.w}x${live.h}`, localWH: `${local.w}x${local.h}`,
        mismatched, pct: (100 * (total - mismatched)) / total });
    } catch (e) {
      rows.push({ width, err: e.message });
    }
    await ctx.close();
  }
  await browser.close();

  console.log("\n=== HydraDB footer pixel diff (video region masked) ===");
  for (const r of rows) {
    if (r.err) { console.log(`${String(r.width).padStart(4)}px  ERROR: ${r.err}`); continue; }
    const flag = r.pct >= 99.5 ? "PASS" : "----";
    console.log(`${String(r.width).padStart(4)}px  ${flag}  match ${r.pct.toFixed(2)}%  (${r.mismatched} px differ / ${r.W}x${r.H})  live=${r.liveWH} local=${r.localWH}`);
  }
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(rows, null, 2));
}

run().catch((e) => { console.error(e); process.exit(1); });
