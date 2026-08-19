import { chromium } from "playwright";
const targets = {
  standalone: "http://localhost:8799/footer-harness/harness.html",
  mintlify: "http://localhost:3111/",
};
const browser = await chromium.launch();
for (const [name, url] of Object.entries(targets)) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(name === "mintlify" ? 3500 : 500);
  const r = await page.evaluate(() => {
    const g = (sel) => { const e = document.querySelector(sel); return e ? getComputedStyle(e).color : "N/A"; };
    return {
      link: g(".hdb-footer__link"),
      head: g(".hdb-footer__head"),
      tagline: g(".hdb-footer__tagline"),
      sociallabel: g(".hdb-footer__social-label"),
      copy: g(".hdb-footer__copy p"),
    };
  });
  console.log(name, JSON.stringify(r));
  await ctx.close();
}
await browser.close();
