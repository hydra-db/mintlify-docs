/* Accessibility spot-check of the footer as rendered in the Mintlify shell. */
import { chromium } from "playwright";
const B = process.env.MINT || "http://localhost:3111";
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await page.goto(B + "/", { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(3500);

const a11y = await page.evaluate(() => {
  const f = document.getElementById("hdb-footer-root");
  if (!f) return { error: "no footer" };
  const cs = (el, p) => getComputedStyle(el)[p];
  return {
    footerRole: f.getAttribute("role"),
    footerAriaLabel: f.getAttribute("aria-label"),
    navLabels: [...f.querySelectorAll("nav")].map((n) => n.getAttribute("aria-label")),
    lists: f.querySelectorAll("ul").length,
    listItems: f.querySelectorAll("li").length,
    logoLabel: f.querySelector(".hdb-footer__logo")?.getAttribute("aria-label"),
    socials: [...f.querySelectorAll(".hdb-footer__social-link")].map((a) => ({
      name: a.getAttribute("aria-label"),
      rel: a.getAttribute("rel"),
      target: a.getAttribute("target"),
      iconHidden: a.querySelector("svg")?.getAttribute("aria-hidden"),
    })),
    externalNewTab: [...f.querySelectorAll('a[target="_blank"]')].map((a) => ({
      text: (a.textContent || "").trim().slice(0, 16), rel: a.getAttribute("rel"),
    })).slice(0, 8),
    // contrast sanity: computed colors on black
    linkColor: cs(f.querySelector(".hdb-footer__link"), "color"),
    focusRuleColor: (() => { const a = f.querySelector("a"); a.focus(); return cs(a, "outlineColor"); })(),
  };
});
console.log(JSON.stringify(a11y, null, 2));
await browser.close();
