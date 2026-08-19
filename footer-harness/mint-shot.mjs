/* Visual proof: footer in the Mintlify shell (dark + light), plus reduced-motion video state. */
import { chromium } from "playwright";
import fs from "node:fs";
const B = process.env.MINT || "http://localhost:3111";
const browser = await chromium.launch();

async function shot(name, reduced) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1,
    reducedMotion: reduced ? "reduce" : "no-preference",
  });
  const page = await ctx.newPage();
  await page.goto(B + "/", { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(3500);
  await page.evaluate(() => document.getElementById("hdb-footer-root")?.scrollIntoView());
  await page.waitForTimeout(800);
  const info = await page.evaluate(() => {
    const v = document.querySelector(".hdb-footer__video-el");
    return { theme: document.documentElement.className.match(/dark|light/)?.[0] || "?",
             videoPaused: v ? v.paused : null, hasAutoplayAttr: v ? v.hasAttribute("autoplay") : null,
             poster: v ? !!v.getAttribute("poster") : null };
  });
  await page.locator("#hdb-footer-root").screenshot({ path: `out/${name}.png` });
  console.log(name, JSON.stringify(info));
  await ctx.close();
}

// Dark (default)
await shot("mint-dark", false);

// Light mode: force the docs theme to light, footer must stay dark.
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.goto(B + "/", { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.evaluate(() => { document.documentElement.classList.remove("dark"); document.documentElement.classList.add("light"); document.documentElement.setAttribute("data-theme","light"); });
  await page.waitForTimeout(500);
  await page.evaluate(() => document.getElementById("hdb-footer-root")?.scrollIntoView());
  await page.waitForTimeout(600);
  await page.locator("#hdb-footer-root").screenshot({ path: "out/mint-light.png" });
  console.log("mint-light captured (docs forced to light)");
  await ctx.close();
}

// Reduced motion
await shot("mint-reduced", true);

await browser.close();
