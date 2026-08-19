/* Verify the footer injects + survives navigation inside the real Mintlify dev shell. */
import { chromium } from "playwright";
const B = process.env.MINT || "http://localhost:3111";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 160)); });
page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message.slice(0, 160)));

await page.goto(B + "/", { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(3500); // allow hydration + Mintlify's custom-JS injection

const snap = () => page.evaluate(() => {
  const f = document.getElementById("hdb-footer-root");
  const b = f && f.getBoundingClientRect();
  return {
    hasReo: typeof window.Reo !== "undefined",
    footerPresent: !!f,
    cssPresent: !!document.getElementById("hdb-footer-css"),
    footerScriptTag: [...document.querySelectorAll('script[src*="footer.js"]')].length,
    fullBleed: b ? (Math.round(b.left) === 0 && Math.round(b.width) === document.documentElement.clientWidth) : null,
    rect: b ? { x: Math.round(b.left), w: Math.round(b.width), h: Math.round(b.height) } : null,
    atPageBottom: f ? (f.getBoundingClientRect().bottom + window.scrollY >= document.body.scrollHeight - 5) : null,
    linkCount: f ? f.querySelectorAll("a").length : 0,
  };
});

console.log("HOME:", JSON.stringify(await snap()));

// Client-side SPA nav: click the first sidebar link, then re-check (tests re-mount).
const beforeUrl = page.url();
const clicked = await page.evaluate(() => {
  const a = document.querySelector('#sidebar a[href], nav a[href^="/"]');
  if (a) { a.click(); return a.getAttribute("href"); }
  return null;
});
await page.waitForTimeout(2500);
console.log("SPA nav clicked:", clicked, "url changed:", page.url() !== beforeUrl);
console.log("AFTER SPA NAV:", JSON.stringify(await snap()));

// Hard load of a deep route.
await page.goto(B + "/essentials/v2/architecture", { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(3000);
console.log("DEEP ROUTE:", JSON.stringify(await snap()));

console.log("CONSOLE ERRORS:", errors.length ? errors.slice(0, 8) : "none");
await browser.close();
