# HydraDB footer (docs.hydradb.com)

A pixel-faithful port of the Framer **`Footer Main`** component from `hydradb.com`, injected into the Mintlify docs site.

## How it's wired

Mintlify is a hosted, config-driven platform — it can't mount a React component as page chrome, but it **auto-executes root-level `.js` and auto-loads root `.css`** (the same mechanism the existing `reo.js` uses). So the footer ships as:

| File | Role |
|---|---|
| [`/footer.js`](../footer.js) | Injects the footer. Holds the content model (`LINK_GROUPS`, `SOCIALS`), builds semantic markup, injects `<link>` to the CSS, mounts full-bleed at the bottom of `<body>`, re-mounts on client-side navigation (Mintlify is a Next.js SPA), and handles `prefers-reduced-motion`. |
| `/footer/footer.css` | All styles. Self-hosted `@font-face`, tokens, box model, the assembled 1px dividers, the grid, and the 5 responsive breakpoints. |
| `/footer/*` | Self-hosted assets: `logo.png`, `iso.png`, `aicpa.png`, `pixel-tree.mp4`, `pixel-tree-poster.png`, `fonts/GeistPixelSquare.woff2`, `fonts/GeistPixel.woff2`. Nothing is hotlinked from Framer's CDN. |

**To change a link:** edit the `LINK_GROUPS` / `SOCIALS` const at the top of `footer.js` — one line per link. `external: true` sets `target="_blank" rel="noopener noreferrer"`.

## Verification harness

`footer-harness/` screenshot-diffs the local footer against the live Framer footer at all 5 breakpoints. Run:

```bash
python3 -m http.server 8799            # from the repo root
cd footer-harness && npm install && npx playwright install chromium
node diff.mjs                          # pixel diff → footer-harness/out/
node measure.mjs                       # dump live footer geometry per breakpoint
```

### Per-breakpoint results (video region masked)

| Width | Framer variant | Match | Status |
|------:|---|------:|:--|
| 1920 | Large | **99.70%** | ✅ ≥99.5% |
| 1440 | Desktop | **99.63%** | ✅ ≥99.5% |
| 1200 | Small | 99.27% | see note ¹ |
| 810 | Tablet | **99.56%** | ✅ ≥99.5% |
| 390 | Phone | 97.24% | see note ² |

Remaining delta is confined to text anti-aliasing and the two per-breakpoint layout quirks below. Verified in the real `mintlify dev` shell: injects on every route incl. client-side SPA nav, full-bleed, dark **and** light (stays dark by design), reduced-motion pauses the video and shows the poster, zero console errors, no CSS bleed from Mintlify's globals.

¹ **1200 (col4 quirk):** In Framer at 1200px the 4th column (Trust/Privacy/Terms) wraps *underneath* the Benchmark column. We keep all four columns top-aligned and consistent instead (cleaner, and you flagged the col4 alignment as confusing). Functional, no overflow.

² **390 (phone):** The tall single-column stack matches structurally; residual is sub-pixel distribution over ~30 rows plus a tagline that wraps to 2 lines vs Framer's 3. Forcing 3 lines shifted the (masked) video region and made the diff worse, so we left it.

---

## Extraction

**What worked:** the entire spec was read from the **published DOM on `hydradb.com`** via headless Chromium (`getComputedStyle`, inline `--border-*` custom props, `@font-face` and `@media` rules). The documented `getNodeXml`-on-component-root failure never came up, and **the Framer project was not touched** (no scratch page, not published) — the DOM is the source of truth anyway.

## Framer-vs-DOM discrepancies (DOM won)

- **Fonts:** the footer uses **`Geist Pixel Square`** (Framer-hosted woff2) + a **distinct `Geist Pixel`** served from **Google `gstatic`** (the 14px nav links). **No Aeonik anywhere in the footer.**
- **Tokens:** dividers are `rgb(53,53,53)` (not the brief's grey-800 `47,49,49`); grid lines `rgb(32,32,32)`; type ramp is **20 / 18 / 16 / 14px** (headers are 18px, not the doc's 16px guess).
- **Letter-spacing** is a uniform `-0.01em` *per element* (so it resolves to -0.2 / -0.18 / -0.16 / -0.14px by size) — but Framer applies it inconsistently: the *Use Cases* & *Benchmark* headers, the *Use Cases* links, and *FinanceBench* use `normal`. Replicated exactly.
- **Grid phase:** Framer centers the 32px grid tile, so the line phase is width-dependent (x=16 at 1920, x=0 at 1440). Replicated with `background-position: center`.
- **Borders:** Framer draws them as pseudo-elements (zero layout impact). Ours do the same, plus the full-bleed top/bottom hairline that spans past the card into the grid margins.
- **Links:** internal links are *relative* (`./#hero`) on Framer; since docs is a different origin we made them absolute `https://hydradb.com/…`. `target="_blank"` appears **only on absolute-URL links** (incl. the *Use Cases*/*Benchmark* headers); Framer's `rel=""` upgraded to `noopener noreferrer`.

## Flagged items (need your call)

1. **`http://` Trust Centre** — ported as-is per your note; it 301-redirects to `https://trust.hydradb.com/`. Recommend switching the source to `https://` to drop the redirect.
2. **Benchmark host mismatch** — the *Benchmark* header → `benchmarks.hydradb.com/` (200), but its 3 children → `research.hydradb.com/…`, which **301-redirects to `benchmarks.hydradb.com/…`**. Ported exactly as-is; do you want them normalized to one host?
3. **`Aeonik TRIAL Regular` licensing** — **moot for the footer** (not used). Only the two Geist Pixel faces are self-hosted here. Flagging since the brief raised it.
4. **`Geist Pixel` (Google)** wasn't in the brief's font list but *is* used by the live footer — now self-hosted.
5. **Video:** the live `<video>` has `autoplay=false` (playback is JS-driven) and no `poster`. Ours uses `autoplay muted loop playsinline`, a generated poster, and **added** `prefers-reduced-motion` pause→poster.
6. **Added (Framer had neither):** semantic landmarks (`<footer role=contentinfo>`, per-column `<nav aria-label>`, `<ul>/<li>`) and visible AA focus rings (`#ff571a`).
7. **Logo asset** wasn't in the brief's asset list — found it (`yQ3d6…png`, hi-res 1180×215) and self-hosted.
8. **Link check:** all 20 targets resolve (200). LinkedIn returns `999` to non-browser requests (bot-blocking) — the URL is valid.

## Scope note

Per your decision this shipped to **docs.hydradb.com only** (Mintlify). benchmarks / research / hackhydra were explicitly out of scope.
