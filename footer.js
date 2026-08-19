/*
 * HydraDB site footer — injected on docs.hydradb.com (Mintlify).
 *
 * Pixel-faithful port of the Framer "Footer Main" component on hydradb.com,
 * extracted from the published DOM (the source of truth). Mintlify is a hosted,
 * config-driven platform with no React chrome we can mount into, so the footer
 * is injected via this root-level custom script (auto-loaded, like reo.js) plus
 * a self-hosted stylesheet + assets under /footer/.
 *
 * Content is config-driven: edit LINK_GROUPS / SOCIALS below to change links.
 */
(function () {
  "use strict";

  var ROOT_ID = "hdb-footer-root";
  var CSS_ID = "hdb-footer-css";
  var CSS_HREF = "/footer/footer.css";
  var ASSETS = "/footer/";

  /* ---- Content model (single source of truth) ------------------------------
   * `external: true` mirrors the Framer original exactly: only links that Framer
   * authored as absolute URLs open in a new tab (target=_blank). Internal links
   * were relative on hydradb.com; here they must be absolute (different origin).
   */
  var LINK_GROUPS = [
    {
      header: { label: "Home", href: "https://hydradb.com/#hero" },
      links: [
        { label: "Why HydraDB", href: "https://hydradb.com/#why-hydradb" },
        { label: "Features", href: "https://hydradb.com/#features" },
        { label: "Architecture", href: "https://hydradb.com/#Architecture" },
        { label: "Pricing", href: "https://hydradb.com/#pricing" },
        { label: "FAQs", href: "https://hydradb.com/#faq" },
        { label: "Contact", href: "https://hydradb.com/contact" }
      ]
    },
    {
      header: { label: "Use Cases", href: "https://hydradb.com/use-cases", external: true },
      links: [
        { label: "Customer Support", href: "https://hydradb.com/use-cases/ai-agent-memory-for-customer-support" },
        { label: "Research Intelligence", href: "https://hydradb.com/use-cases/ai-agent-memory-for-research-intelligence" },
        { label: "AI Coding Assistants", href: "https://hydradb.com/use-cases/ai-coding-assistant-memory-layer" }
      ]
    },
    {
      header: { label: "Benchmark", href: "https://benchmarks.hydradb.com/", external: true },
      links: [
        { label: "Main Paper", href: "https://research.hydradb.com/hydradb", external: true },
        { label: "BEAM 1M", href: "https://research.hydradb.com/beam-1m", external: true },
        { label: "FinanceBench", href: "https://research.hydradb.com/financebench", external: true }
      ]
    },
    {
      header: null,
      ariaLabel: "Legal and compliance",
      links: [
        { label: "Trust Centre", href: "http://trust.hydradb.com/", external: true },
        { label: "Privacy Policy", href: "https://hydradb.com/privacy-policy" },
        { label: "Terms of Service", href: "https://hydradb.com/terms-of-service" }
      ]
    }
  ];

  var SOCIALS = [
    { label: "X / Twitter", href: "https://x.com/Hydra_DB", icon: "x" },
    { label: "LinkedIn", href: "https://www.linkedin.com/company/hydradb/posts/?feedView=all", icon: "linkedin" },
    { label: "Discord", href: "https://discord.gg/rM64NhGe7", icon: "discord" }
  ];

  /* Icons reproduced verbatim from the Framer footer's inline SVGs. */
  var ICONS = {
    x: '<svg class="hdb-footer__icon" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M 21 2.25 C 21.414 2.25 21.75 2.586 21.75 3 L 21.75 21 C 21.75 21.414 21.414 21.75 21 21.75 L 3 21.75 C 2.586 21.75 2.25 21.414 2.25 21 L 2.25 3 C 2.25 2.586 2.586 2.25 3 2.25 Z M 5.939 17 L 7 18.061 L 11.101 13.96 L 13.839 17.75 L 18.467 17.75 L 13.788 11.272 L 18.061 7 L 17 5.939 L 12.899 10.041 L 10.161 6.25 L 5.533 6.25 L 10.212 12.728 Z"/></svg>',
    linkedin: '<svg class="hdb-footer__icon" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M 21.75 2.25 L 2.25 2.25 L 2.25 21.75 L 21.75 21.75 Z M 7 7.75 C 6.448 7.75 6 7.302 6 6.75 C 6 6.198 6.448 5.75 7 5.75 C 7.552 5.75 8 6.198 8 6.75 C 8 7.302 7.552 7.75 7 7.75 Z M 6 9 L 8 9 L 8 18 L 6 18 Z M 10 9 L 12 9 L 12 9.537 C 12.588 9.196 13.271 9 14 9 C 16.209 9 18 10.791 18 13 L 18 18 L 16 18 L 16 13 C 16 11.895 15.105 11 14 11 C 12.895 11 12 11.895 12 13 L 12 18 L 10 18 Z"/></svg>',
    discord: '<svg class="hdb-footer__icon" viewBox="0 0 16 16" width="24" height="24" aria-hidden="true" focusable="false"><path fill="currentColor" transform="translate(0 1.902)" d="M 13.545 1.011 C 12.508 0.535 11.413 0.195 10.288 0.001 C 10.267 -0.003 10.246 0.007 10.236 0.026 C 10.096 0.276 9.94 0.602 9.83 0.859 C 8.618 0.675 7.385 0.675 6.172 0.859 C 6.05 0.574 5.913 0.296 5.761 0.026 C 5.75 0.008 5.729 -0.002 5.708 0.001 C 4.583 0.195 3.489 0.534 2.452 1.011 C 2.443 1.015 2.436 1.021 2.43 1.029 C 0.356 4.128 -0.213 7.151 0.066 10.136 C 0.068 10.151 0.075 10.164 0.087 10.174 C 1.295 11.068 2.646 11.751 4.082 12.194 C 4.103 12.199 4.125 12.192 4.138 12.175 C 4.447 11.755 4.721 11.311 4.956 10.846 C 4.962 10.833 4.963 10.818 4.958 10.804 C 4.953 10.791 4.942 10.78 4.928 10.775 C 4.497 10.61 4.08 10.411 3.68 10.18 C 3.665 10.172 3.656 10.156 3.655 10.139 C 3.654 10.122 3.661 10.105 3.675 10.095 C 3.759 10.032 3.842 9.967 3.923 9.9 C 3.937 9.888 3.957 9.885 3.974 9.894 C 6.593 11.089 9.428 11.089 12.016 9.894 C 12.033 9.885 12.053 9.888 12.068 9.9 C 12.148 9.966 12.231 10.031 12.316 10.095 C 12.33 10.105 12.337 10.121 12.337 10.138 C 12.336 10.155 12.327 10.171 12.312 10.18 C 11.914 10.413 11.496 10.612 11.064 10.774 C 11.05 10.779 11.039 10.79 11.034 10.804 C 11.029 10.817 11.03 10.833 11.036 10.846 C 11.276 11.311 11.551 11.754 11.853 12.174 C 11.866 12.191 11.888 12.199 11.909 12.193 C 13.348 11.753 14.701 11.069 15.91 10.173 C 15.922 10.164 15.93 10.151 15.932 10.137 C 16.265 6.686 15.373 3.688 13.566 1.03 C 13.561 1.021 13.554 1.014 13.545 1.01 M 5.347 8.318 C 4.559 8.318 3.909 7.594 3.909 6.705 C 3.909 5.816 4.546 5.092 5.347 5.092 C 6.154 5.092 6.798 5.823 6.785 6.706 C 6.785 7.594 6.148 8.318 5.347 8.318 M 10.664 8.318 C 9.875 8.318 9.226 7.594 9.226 6.705 C 9.226 5.816 9.862 5.092 10.664 5.092 C 11.47 5.092 12.114 5.823 12.102 6.706 C 12.102 7.594 11.471 8.318 10.664 8.318"/></svg>'
  };

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function anchor(label, href, external, cls) {
    var attrs = 'class="' + cls + '" href="' + esc(href) + '"';
    if (external) attrs += ' target="_blank" rel="noopener noreferrer"';
    return "<a " + attrs + ">" + esc(label) + "</a>";
  }

  function buildHTML() {
    var cols = LINK_GROUPS.map(function (g) {
      var head = g.header
        ? anchor(g.header.label, g.header.href, g.header.external, "hdb-footer__head")
        : "";
      var items = g.links.map(function (l) {
        return "<li>" + anchor(l.label, l.href, l.external, "hdb-footer__link") + "</li>";
      }).join("");
      var aria = g.header ? g.header.label : (g.ariaLabel || "Links");
      var mod = g.header ? "" : " hdb-footer__col--noheader";
      return '<nav class="hdb-footer__col' + mod + '" aria-label="' + esc(aria) + '">' +
        head + '<ul class="hdb-footer__list">' + items + "</ul></nav>";
    }).join("");

    var socials = SOCIALS.map(function (s) {
      return '<a class="hdb-footer__social-link" href="' + esc(s.href) +
        '" target="_blank" rel="noopener noreferrer" aria-label="' + esc(s.label) + '">' +
        '<span class="hdb-footer__social-label">' + esc(s.label) + "</span>" +
        (ICONS[s.icon] || "") + "</a>";
    }).join("");

    return "" +
      '<div class="hdb-footer__grid" aria-hidden="true"></div>' +
      '<div class="hdb-footer__card">' +
        '<div class="hdb-footer__left">' +
          '<div class="hdb-footer__brand">' +
            '<div class="hdb-footer__brand-inner">' +
              '<a class="hdb-footer__logo" href="https://hydradb.com/#hero" aria-label="HydraDB home">' +
                '<img src="' + ASSETS + 'logo.png" width="180" height="33" alt="HydraDB"/></a>' +
              '<p class="hdb-footer__tagline">Build AI with compounding intelligence</p>' +
            "</div>" +
          "</div>" +
          '<div class="hdb-footer__cols">' +
            '<div class="hdb-footer__cols-row">' + cols + "</div>" +
            '<div class="hdb-footer__badges">' +
              '<img class="hdb-footer__badge" src="' + ASSETS + 'iso.png" width="52" height="52" alt="ISO 27001 certified" loading="lazy"/>' +
              '<img class="hdb-footer__badge" src="' + ASSETS + 'aicpa.png" width="52" height="52" alt="AICPA SOC 2 certified" loading="lazy"/>' +
            "</div>" +
          "</div>" +
        "</div>" +
        '<div class="hdb-footer__right">' +
          '<div class="hdb-footer__social">' + socials + "</div>" +
          '<div class="hdb-footer__video">' +
            '<video class="hdb-footer__video-el" autoplay muted loop playsinline preload="metadata" poster="' + ASSETS + 'pixel-tree-poster.png">' +
              '<source src="' + ASSETS + 'pixel-tree.mp4" type="video/mp4"/>' +
            "</video>" +
          "</div>" +
          '<div class="hdb-footer__copy"><p>&copy; 2026 AGI Context, Inc</p></div>' +
        "</div>" +
      "</div>";
  }

  function ensureCSS() {
    if (document.getElementById(CSS_ID)) return;
    var link = document.createElement("link");
    link.id = CSS_ID;
    link.rel = "stylesheet";
    link.href = CSS_HREF;
    (document.head || document.documentElement).appendChild(link);
  }

  var prefersReduced = null;
  function reducedMotion() {
    if (!prefersReduced && window.matchMedia) {
      prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    }
    return prefersReduced && prefersReduced.matches;
  }

  function applyMotion(root) {
    var vid = root.querySelector(".hdb-footer__video-el");
    if (!vid) return;
    if (reducedMotion()) {
      vid.removeAttribute("autoplay");
      try { vid.pause(); } catch (e) {}
    } else {
      var p = vid.play();
      if (p && p.catch) p.catch(function () {});
    }
  }

  /* Mount full-bleed. Body-level so the band spans the viewport, unconstrained
   * by the docs content column. */
  function pickMount() {
    return document.body;
  }

  function mount() {
    if (!document.body) return;
    ensureCSS();
    if (document.getElementById(ROOT_ID)) return; // idempotent
    var host = pickMount();
    if (!host) return;
    var footer = document.createElement("footer");
    footer.id = ROOT_ID;
    footer.className = "hdb-footer";
    footer.setAttribute("role", "contentinfo");
    footer.setAttribute("aria-label", "HydraDB");
    footer.innerHTML = buildHTML();
    host.appendChild(footer);
    applyMotion(footer);
  }

  /* Mintlify is a Next.js SPA: content is swapped on navigation and our node can
   * be detached. Re-mount on history changes and as a MutationObserver safety net. */
  var scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    (window.requestAnimationFrame || window.setTimeout)(function () {
      scheduled = false;
      mount();
    }, 0);
  }

  function watch() {
    ["pushState", "replaceState"].forEach(function (m) {
      var orig = history[m];
      if (typeof orig === "function") {
        history[m] = function () {
          var r = orig.apply(this, arguments);
          schedule();
          return r;
        };
      }
    });
    window.addEventListener("popstate", schedule);

    if (window.MutationObserver && document.body) {
      var obs = new MutationObserver(function () {
        if (!document.getElementById(ROOT_ID)) schedule();
      });
      obs.observe(document.body, { childList: true, subtree: false });
    }

    if (prefersReduced) {
      var onChange = function () {
        var f = document.getElementById(ROOT_ID);
        if (f) applyMotion(f);
      };
      if (prefersReduced.addEventListener) prefersReduced.addEventListener("change", onChange);
      else if (prefersReduced.addListener) prefersReduced.addListener(onChange);
    }
  }

  function init() {
    reducedMotion();
    mount();
    watch();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
