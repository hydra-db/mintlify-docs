/*
 * askai.js — HydraDB "Ask AI" widget (drop-in, framework-agnostic).
 *
 * A self-contained launcher + slide-out chat panel that answers questions from
 * your docs with citations, streamed live. Built for Mintlify (auto-executes
 * root-level .js, like footer.js/reo.js) but works in any HTML/docs site.
 *
 * The whole widget lives in a Shadow DOM host, so the host page's CSS can't
 * leak in and the widget's can't leak out — no overrides, no !important wars.
 *
 * ── Configure (one line) ────────────────────────────────────────────────────
 * The only thing you must set is the gateway URL. Either edit DEFAULT_ENDPOINT
 * below, or set a runtime global BEFORE this script:
 *
 *   window.HydraAskAI = {
 *     endpoint: "https://ask.yourdomain.com", // ask API base — the only required field
 *     logo:     "",                           // optional custom logo URL (defaults to HydraDB mark)
 *     theme:    { accent: "#FF571A" },         // optional brand override
 *     modes:    ["fast", "auto", "thinking"], // which think-modes to show
 *   };
 *
 * ── API key: none needed in the browser ─────────────────────────────────────
 * The askai-gateway holds the real HydraDB + LLM keys server-side, so the page
 * ships no secret. `apiKey` is optional and only used if your gateway enforces a
 * public, rate-limited widget token (ASKAI_PUBLIC_KEY).
 *
 * ── Theme (takes the host's colors) ────────────────────────────────────────
 * Precedence: config.theme.<token> → host CSS var --askai-<token> on :root →
 * built-in default. So a host that sets `--askai-accent:#7C3AED` is themed with
 * zero JS. Tokens: accent, panel, bg, text, muted, line.
 */
(function () {
  "use strict";

  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ SELF-HOSTING? This is the only line you must change.                 │
  // │ Point it at your running askai-gateway and the widget just works —   │
  // │ no keys in the browser, no build step, no config file. The gateway   │
  // │ holds the HydraDB + LLM keys server-side.                            │
  // │   • Local dev:   "http://localhost:8080"                             │
  // │   • Production:  "https://ask.yourdomain.com"                        │
  // │ (You can still override at runtime with window.HydraAskAI.endpoint.) │
  // └─────────────────────────────────────────────────────────────────────┘
  var DEFAULT_ENDPOINT = "https://agents.hydradb.com";

  var cfg = window.HydraAskAI || window.ASKAI_CONFIG || {};
  var CONFIG = Object.assign(
    {
      endpoint: DEFAULT_ENDPOINT,
      apiKey: "",
      logo: "",
      title: "Ask AI",
      placeholder: "Ask about the docs…",
      greeting:
        "Hi! I can answer questions from the documentation, with sources. What are you looking for?",
      modes: ["fast", "auto", "thinking"],
      defaultMode: "auto",
      brand: true, // attribution is always shown (Kapa-style); brandUrl preserved for link
      brandUrl: "https://hydradb.com",
      position: "right", // reserved
      theme: {},
    },
    cfg
  );

  // API key: OPTIONAL. Default is none — the gateway holds the real HydraDB and
  // LLM keys server-side, so the browser needs no secret. Only set this if your
  // gateway enforces a *public* widget token (ASKAI_PUBLIC_KEY) for abuse control.
  var API_KEY = CONFIG.apiKey || window.HYDRA_ASKAI_KEY || "";

  // Keyboard shortcut label (⌘I on macOS, Ctrl+I elsewhere). The widget takes
  // over Cmd/Ctrl+I from the host's built-in assistant — see the capture-phase
  // handler and hideHostAssistant() below.
  var IS_MAC = /Mac|iPhone|iPad|iPod/.test(
    (navigator.platform || "") + " " + (navigator.userAgent || "")
  );
  var SHORTCUT = IS_MAC ? "⌘I" : "Ctrl+I";

  var HOST_ID = "hydra-askai-root";
  var MODE_LABEL = { fast: "Fast", auto: "Auto", thinking: "Thinking" };
  var MODE_HINT = {
    fast: "Quick answer, lightest retrieval",
    auto: "Balances speed and depth automatically",
    thinking: "Deeper retrieval with graph context",
  };

  // Theme token → default. Host may override via config.theme or CSS var on :root.
  var THEME_DEFAULTS = {
    accent: "#FF571A",
    panel: "#14141b",
    bg: "#0b0b10",
    text: "#ececf1",
    muted: "#9a9aa8",
    line: "rgba(255,255,255,0.10)",
  };

  function resolveTheme() {
    var rootStyles = null;
    try {
      rootStyles = getComputedStyle(document.documentElement);
    } catch (e) {}
    var t = {};
    Object.keys(THEME_DEFAULTS).forEach(function (k) {
      var fromCfg = CONFIG.theme && CONFIG.theme[k];
      var fromVar = rootStyles
        ? rootStyles.getPropertyValue("--askai-" + k).trim()
        : "";
      t[k] = fromCfg || fromVar || THEME_DEFAULTS[k];
    });
    return t;
  }

  function styleFor(t) {
    return `
    :host { all: initial; }
    *, *::before, *::after { box-sizing: border-box; }
    .wrap {
      font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      --accent: ${t.accent};
      --accent-soft: color-mix(in srgb, ${t.accent} 14%, transparent);
      --accent-line: color-mix(in srgb, ${t.accent} 30%, transparent);
      --bg: ${t.bg}; --panel: ${t.panel};
      --line: ${t.line}; --text: ${t.text}; --muted: ${t.muted};
    }
    .launcher {
      position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
      display: inline-flex; align-items: center; gap: 9px;
      padding: 10px 16px; border: none; border-radius: 999px; cursor: pointer;
      background: var(--accent); color: #fff; font-size: 14px; font-weight: 600;
      box-shadow: 0 6px 24px var(--accent-line);
      transition: transform .15s ease, box-shadow .15s ease;
    }
    .launcher:hover { transform: translateY(-1px); }
    .launcher-kbd {
      font: inherit; font-size: 11px; font-weight: 600; line-height: 1;
      padding: 3px 6px; border-radius: 6px; color: #fff;
      background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.25);
    }
    @media (max-width: 520px) { .launcher-kbd { display: none; } }
    .logo-wrap { display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
    .logo-img { display: block; object-fit: contain; flex-shrink: 0; }
    .launcher-logo { width: 18px; height: 18px; border-radius: 4px; overflow: hidden; }

    .scrim {
      position: fixed; inset: 0; z-index: 2147483200; background: rgba(0,0,0,0.45);
      opacity: 0; pointer-events: none; transition: opacity .2s ease;
    }
    .scrim.open { opacity: 1; pointer-events: auto; }

    .panel {
      position: fixed; top: 0; right: 0; height: 100%; height: 100dvh;
      width: 440px; max-width: 100vw; z-index: 2147483201;
      background: var(--panel); color: var(--text); border-left: 1px solid var(--line);
      display: flex; flex-direction: column;
      transform: translateX(100%); transition: transform .24s cubic-bezier(.4,0,.2,1);
      box-shadow: -20px 0 60px rgba(0,0,0,0.4);
    }
    .panel.open { transform: translateX(0); }

    .head { display: flex; align-items: center; gap: 10px; padding: 15px 16px;
      border-bottom: 1px solid var(--line); flex: 0 0 auto; }
    .head-logo { width: 20px; height: 20px; border-radius: 4px; overflow: hidden; }
    .head h2 { margin: 0; font-size: 15px; font-weight: 600; }
    .head .spacer { flex: 1; }
    .head button.close { background: transparent; border: none; color: var(--muted);
      cursor: pointer; font-size: 22px; line-height: 1; padding: 2px 8px; border-radius: 8px; }
    .head button.close:hover { color: var(--text); background: rgba(255,255,255,0.06); }

    .modes { display: flex; gap: 4px; padding: 10px 16px 4px; flex: 0 0 auto; }
    .modes button { flex: 1; padding: 6px 8px; font-size: 12px; font-weight: 600; cursor: pointer;
      background: transparent; color: var(--muted); border: 1px solid var(--line); border-radius: 8px;
      transition: all .12s ease; }
    .modes button:hover { color: var(--text); }
    .modes button.active { color: var(--accent); background: var(--accent-soft); border-color: var(--accent-line); }
    .modehint { padding: 3px 16px 0; font-size: 11px; color: var(--muted); flex: 0 0 auto; min-height: 15px; }

    .thread { flex: 1 1 auto; overflow-y: auto; padding: 16px; display: flex;
      flex-direction: column; gap: 15px; }
    .msg { display: flex; flex-direction: column; gap: 6px; max-width: 100%; }
    .msg .role { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
    .bubble { font-size: 14px; line-height: 1.62; }
    .msg.user { align-items: flex-end; }
    .msg.user .bubble { background: var(--accent-soft); border: 1px solid var(--accent-line);
      color: var(--text); padding: 9px 13px; border-radius: 12px 12px 2px 12px; max-width: 88%; }
    .bubble p { margin: 0 0 10px; } .bubble p:last-child { margin-bottom: 0; }
    .bubble ul { margin: 0 0 10px; padding-left: 20px; } .bubble li { margin: 2px 0; }
    .bubble pre { background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
      padding: 12px; overflow-x: auto; font-size: 12.5px; }
    .bubble code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(255,255,255,0.07); padding: 1px 5px; border-radius: 5px; font-size: 12.5px; }
    .bubble pre code { background: none; padding: 0; }
    .cite { display: inline-flex; align-items: center; justify-content: center; min-width: 17px;
      height: 17px; padding: 0 4px; margin: 0 1px; font-size: 10px; font-weight: 700; color: var(--accent);
      background: var(--accent-soft); border-radius: 5px; cursor: pointer; text-decoration: none; }

    .dots { display: inline-flex; gap: 4px; align-items: center; height: 18px; }
    .dots span { width: 6px; height: 6px; border-radius: 50%; background: var(--muted);
      animation: blink 1.2s infinite ease-in-out both; }
    .dots span:nth-child(2) { animation-delay: .2s; } .dots span:nth-child(3) { animation-delay: .4s; }
    @keyframes blink { 0%,80%,100% { opacity: .25; } 40% { opacity: 1; } }

    .sources { margin-top: 10px; border-top: 1px solid var(--line); padding-top: 10px; }
    .sources h4 { margin: 0 0 8px; font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
    .source { display: flex; gap: 8px; align-items: baseline; padding: 6px 8px; border-radius: 8px;
      text-decoration: none; color: var(--text); }
    .source:hover { background: rgba(255,255,255,0.05); }
    .source .n { color: var(--accent); font-weight: 700; font-size: 11px; min-width: 16px; }
    .source .t { font-size: 13px; }

    .composer { flex: 0 0 auto; border-top: 1px solid var(--line); padding: 12px;
      display: flex; gap: 8px; align-items: flex-end; }
    .composer textarea { flex: 1; resize: none; max-height: 120px; min-height: 42px; padding: 10px 12px;
      background: var(--bg); border: 1px solid var(--line); border-radius: 10px; color: var(--text);
      font: inherit; font-size: 14px; line-height: 1.4; outline: none; }
    .composer textarea:focus { border-color: var(--accent-line); }
    .composer button.send { flex: 0 0 auto; width: 42px; height: 42px; border-radius: 10px; border: none;
      cursor: pointer; background: var(--accent); color: #fff; display: inline-flex; align-items: center; justify-content: center; }
    .composer button.send:disabled { opacity: .45; cursor: default; }
    .composer button.send svg { width: 18px; height: 18px; }

    .foot { padding: 0 14px 10px; display: flex; align-items: center; justify-content: center; gap: 5px;
      font-size: 11px; color: var(--muted); flex: 0 0 auto; flex-wrap: wrap; text-align: center; }
    .foot .foot-disclaimer { color: var(--muted); opacity: 0.85; }
    .foot .foot-powered { display: inline-flex; align-items: center; gap: 4px; }
    .foot .brand-link { display: inline-flex; align-items: center; gap: 4px; color: var(--accent); text-decoration: none; font-weight: 600; }
    .foot .brand-link:hover { text-decoration: underline; color: #fff; }
    .foot-logo { width: 12px; height: 12px; border-radius: 2.5px; overflow: hidden; display: inline-flex; }

    /* ── Responsive ──────────────────────────────────────────────────────── */
    /* Large screens: a touch wider for comfortable reading. */
    @media (min-width: 1280px) { .panel { width: 460px; } }
    /* Medium screens / tablets. */
    @media (max-width: 1024px) { .panel { width: 400px; } }
    /* Small tablets / large phones: near-full but leave a peek of the page. */
    @media (max-width: 720px) { .panel { width: 88vw; } .launcher span { display: inline; } }
    /* Phones: full-screen sheet, compact launcher. */
    @media (max-width: 520px) {
      .panel { width: 100vw; border-left: none; }
      .launcher { right: 16px; bottom: 16px; padding: 11px 14px; }
      .thread { padding: 14px; }
      .modes button { font-size: 11px; padding: 6px 4px; }
    }
    /* Very short viewports (landscape phones): keep composer reachable. */
    @media (max-height: 480px) { .modehint { display: none; } .foot { display: none; } }
    @media (prefers-reduced-motion: reduce) { .panel, .scrim, .launcher { transition: none; } }
  `;
  }

  var HYDRA_LOGO_SVG =
    '<svg viewBox="0 0 1600 1600" aria-hidden="true" style="width:100%;height:100%;display:block;">' +
    '<path fill="#ffffff" d="M739,1601 C492.67,1601 246.83,1601 1,1601 C1,1067.67 1,534.33 1,1 C534.33,1 1067.67,1 1601,1 C1601,534.33 1601,1067.67 1601,1601 C1313.83,1601 1026.67,1601 739,1601 M1004.5,1209 C1013.74,1209 1022.98,1209 1032.66,1209 C1032.66,1169.69 1032.66,1131.12 1032.66,1092.09 C1071.68,1092.09 1110.23,1092.09 1149.09,1092.09 C1149.09,1053.2 1149.09,1014.76 1149.09,975.75 C1188.09,975.75 1226.78,975.75 1265.41,975.75 C1265.41,858.91 1265.41,742.66 1265.41,626.02 C1226.53,626.02 1187.95,626.02 1148.92,626.02 C1148.92,587.06 1148.92,548.48 1148.92,509.48 C1109.89,509.48 1071.31,509.48 1032.29,509.48 C1032.29,470.49 1032.29,431.92 1032.29,393.35 C876.63,393.35 721.43,393.35 565.8,393.35 C565.8,432.27 565.8,470.84 565.8,509.79 C526.96,509.79 488.54,509.79 449.59,509.79 C449.59,548.72 449.59,587.28 449.59,626.23 C410.47,626.23 371.88,626.23 333.34,626.23 C333.34,743 333.34,859.39 333.34,976.19 C372.26,976.19 410.83,976.19 449.78,976.19 C449.78,1015.03 449.78,1053.45 449.78,1092.4 C488.72,1092.4 527.27,1092.4 566.22,1092.4 C566.22,1131.52 566.22,1170.11 566.22,1209 C712.24,1209 857.87,1209 1004.5,1209 z"/>' +
    '<path fill="#FF571A" d="M1004,1209 C857.87,1209 712.24,1209 566.22,1209 C566.22,1170.11 566.22,1131.52 566.22,1092.4 C527.27,1092.4 488.72,1092.4 449.78,1092.4 C449.78,1053.45 449.78,1015.03 449.78,976.19 C410.83,976.19 372.26,976.19 333.34,976.19 C333.34,859.39 333.34,743 333.34,626.23 C371.88,626.23 410.47,626.23 449.59,626.23 C449.59,587.28 449.59,548.72 449.59,509.79 C488.54,509.79 526.96,509.79 565.8,509.79 C565.8,470.84 565.8,432.27 565.8,393.35 C721.43,393.35 876.63,393.35 1032.29,393.35 C1032.29,431.92 1032.29,470.49 1032.29,509.48 C1071.31,509.48 1109.89,509.48 1148.92,509.48 C1148.92,548.48 1148.92,587.06 1148.92,626.02 C1187.95,626.02 1226.53,626.02 1265.41,626.02 C1265.41,742.66 1265.41,858.91 1265.41,975.75 C1226.78,975.75 1188.09,975.75 1149.09,975.75 C1149.09,1014.76 1149.09,1053.2 1149.09,1092.09 C1110.23,1092.09 1071.68,1092.09 1032.66,1092.09 C1032.66,1131.12 1032.66,1169.69 1032.66,1209 C1022.98,1209 1013.74,1209 1004,1209 M722.5,509.7 C709.4,509.7 696.29,509.7 682.69,509.7 C682.69,548.76 682.69,587.32 682.69,626.26 C643.59,626.26 605,626.26 566,626.26 C566,665.23 566,703.81 566,742.91 C527.05,742.91 488.49,742.91 449.9,742.91 C449.9,781.85 449.9,820.28 449.9,859.21 C488.65,859.21 527.2,859.21 566.15,859.21 C566.15,898.31 566.15,936.9 566.15,976.07 C605.23,976.07 643.93,976.07 682.97,976.07 C682.97,1015.09 682.97,1053.52 682.97,1092.05 C760.72,1092.05 838.12,1092.05 916.01,1092.05 C916.01,1053.26 916.01,1014.7 916.01,975.83 C954.95,975.83 993.38,975.83 1032.44,975.83 C1032.44,936.81 1032.44,898.1 1032.44,858.98 C1071.63,858.98 1110.2,858.98 1148.63,858.98 C1148.63,820.01 1148.63,781.46 1148.63,742.5 C1109.69,742.5 1071.12,742.5 1032.1,742.5 C1032.1,703.51 1032.1,664.94 1032.1,625.86 C993.11,625.86 954.56,625.86 915.71,625.86 C915.71,586.91 915.71,548.47 915.71,509.7 C851.43,509.7 787.47,509.7 722.5,509.7 z"/>' +
    '<path fill="#ffffff" d="M723,509.7 C787.47,509.7 851.43,509.7 915.71,509.7 C915.71,548.47 915.71,586.91 915.71,625.86 C954.56,625.86 993.11,625.86 1032.1,625.86 C1032.1,664.94 1032.1,703.51 1032.1,742.5 C1071.12,742.5 1109.69,742.5 1148.63,742.5 C1148.63,781.46 1148.63,820.01 1148.63,858.98 C1110.2,858.98 1071.63,858.98 1032.44,858.98 C1032.44,898.1 1032.44,936.81 1032.44,975.83 C993.38,975.83 954.95,975.83 916.01,975.83 C916.01,1014.7 916.01,1053.26 916.01,1092.05 C838.12,1092.05 760.72,1092.05 682.97,1092.05 C682.97,1053.52 682.97,1015.09 682.97,976.07 C643.93,976.07 605.23,976.07 566.15,976.07 C566.15,936.9 566.15,898.31 566.15,859.21 C527.2,859.21 488.65,859.21 449.9,859.21 C449.9,820.28 449.9,781.85 449.9,742.91 C488.49,742.91 527.05,742.91 566,742.91 C566,703.81 566,665.23 566,626.26 C605,626.26 643.59,626.26 682.69,626.26 C682.69,587.32 682.69,548.76 682.69,509.7 C696.29,509.7 709.4,509.7 723,509.7 M843.49,742.83 C809.42,742.83 775.35,742.83 741.3,742.83 C741.3,781.92 741.3,820.47 741.3,858.88 C780.17,858.88 818.73,858.88 857.33,858.88 C857.33,820.08 857.33,781.57 857.33,742.83 C852.8,742.83 848.64,742.83 843.49,742.83 z"/>' +
    '<path fill="#FF571A" d="M843.99,742.83 C848.64,742.83 852.8,742.83 857.33,742.83 C857.33,781.57 857.33,820.08 857.33,858.88 C818.73,858.88 780.17,858.88 741.3,858.88 C741.3,820.47 741.3,781.92 741.3,742.83 C775.35,742.83 809.42,742.83 843.99,742.83 z"/>' +
    '</svg>';

  var ICON_SEND =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/></svg>';

  function renderLogo(cls) {
    if (CONFIG.logo) {
      return '<img class="logo-img ' + cls + '" src="' + escapeHtml(CONFIG.logo) + '" alt="" />';
    }
    return '<span class="logo-wrap ' + cls + '">' + HYDRA_LOGO_SVG + '</span>';
  }

  function h(tag, cls, html) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (html != null) el.innerHTML = html;
    return el;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  // Minimal, safe markdown: escape first, then render a small subset. No raw HTML.
  function renderMarkdown(src) {
    var out = escapeHtml(src);
    out = out.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, l, code) {
      return "<pre><code>" + code.replace(/\n$/, "") + "</code></pre>";
    });
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/\[(\d+)\]/g, function (_, n) {
      return '<a class="cite" data-cite="' + n + '" href="#">' + n + "</a>";
    });
    out = out.replace(/(^|\n)[-*] (.+)/g, "$1<li>$2</li>");
    out = out.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>").replace(/<\/ul>\s*<ul>/g, "");
    return out
      .split(/\n{2,}/)
      .map(function (b) {
        b = b.trim();
        if (/^<(pre|ul)/.test(b)) return b;
        return b ? "<p>" + b.replace(/\n/g, "<br>") + "</p>" : "";
      })
      .join("");
  }

  function AskAI(shadow) {
    var busy = false;
    var mode = CONFIG.modes.indexOf(CONFIG.defaultMode) >= 0 ? CONFIG.defaultMode : CONFIG.modes[0];

    var wrap = h("div", "wrap");
    var launcher = h("button", "launcher",
      renderLogo("launcher-logo") + "<span>" + escapeHtml(CONFIG.title) +
      '</span><kbd class="launcher-kbd">' + escapeHtml(SHORTCUT) + "</kbd>");
    launcher.setAttribute("aria-label", "Open " + CONFIG.title + " (" + SHORTCUT + ")");
    launcher.title = CONFIG.title + " — press " + SHORTCUT;
    var scrim = h("div", "scrim");
    var panel = h("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", CONFIG.title);

    var head = h("div", "head",
      renderLogo("head-logo") + '<h2>' + escapeHtml(CONFIG.title) + '</h2><span class="spacer"></span>');
    var closeBtn = h("button", "close", "&times;");
    closeBtn.setAttribute("aria-label", "Close");
    head.appendChild(closeBtn);

    // think-mode segmented control
    var modes = h("div", "modes");
    var modehint = h("div", "modehint", MODE_HINT[mode] || "");
    var modeBtns = {};
    (CONFIG.modes || []).forEach(function (m) {
      var b = h("button", m === mode ? "active" : "", escapeHtml(MODE_LABEL[m] || m));
      b.setAttribute("aria-label", "Mode: " + (MODE_LABEL[m] || m));
      b.onclick = function () {
        mode = m;
        Object.keys(modeBtns).forEach(function (k) { modeBtns[k].classList.toggle("active", k === m); });
        modehint.textContent = MODE_HINT[m] || "";
      };
      modeBtns[m] = b;
      modes.appendChild(b);
    });

    var thread = h("div", "thread");
    var composer = h("div", "composer");
    var textarea = h("textarea");
    textarea.placeholder = CONFIG.placeholder;
    textarea.rows = 1;
    var sendBtn = h("button", "send", ICON_SEND);
    sendBtn.setAttribute("aria-label", "Send");
    composer.appendChild(textarea);
    composer.appendChild(sendBtn);

    var brandHtml =
      '<span class="foot-disclaimer">Answers are AI-generated ·</span>' +
      '<span class="foot-powered">Powered by <a class="brand-link" href="' + escapeHtml(CONFIG.brandUrl || "https://hydradb.com") + '" target="_blank" rel="noopener">' +
      renderLogo("foot-logo") + 'HydraDB</a></span>';
    var foot = h("div", "foot", brandHtml);

    panel.appendChild(head);
    if (CONFIG.modes && CONFIG.modes.length > 1) { panel.appendChild(modes); panel.appendChild(modehint); }
    panel.appendChild(thread);
    panel.appendChild(composer);
    panel.appendChild(foot);
    wrap.appendChild(launcher);
    wrap.appendChild(scrim);
    wrap.appendChild(panel);

    var styleEl = h("style");
    styleEl.textContent = styleFor(resolveTheme());
    shadow.appendChild(styleEl);
    shadow.appendChild(wrap);

    addMessage("assistant", CONFIG.greeting + "\n\nTip: press **" + SHORTCUT + "** anytime to open or close Ask AI.");

    function open() {
      scrim.classList.add("open");
      panel.classList.add("open");
      setTimeout(function () { textarea.focus(); }, 60);
    }
    function close() { scrim.classList.remove("open"); panel.classList.remove("open"); }
    launcher.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    scrim.addEventListener("click", close);
    // Capture phase on window = earliest in the dispatch order, so we intercept
    // Cmd/Ctrl+I before the host's built-in assistant (e.g. Mintlify's) can see
    // it. stopImmediatePropagation keeps the event from reaching that handler.
    window.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && panel.classList.contains("open")) { close(); return; }
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "i") {
        e.preventDefault();
        e.stopImmediatePropagation();
        panel.classList.contains("open") ? close() : open();
      }
    }, true);
    textarea.addEventListener("input", function () {
      textarea.style.height = "auto";
      textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
    });
    textarea.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    });
    sendBtn.addEventListener("click", submit);

    function addMessage(role, text) {
      var msg = h("div", "msg " + role);
      msg.appendChild(h("div", "role", role === "user" ? "You" : CONFIG.title));
      var bubble = h("div", "bubble");
      if (role === "assistant" && text === "") {
        bubble.innerHTML = '<span class="dots"><span></span><span></span><span></span></span>';
      } else {
        bubble.innerHTML = role === "user" ? escapeHtml(text) : renderMarkdown(text);
      }
      msg.appendChild(bubble);
      thread.appendChild(msg);
      thread.scrollTop = thread.scrollHeight;
      return { msg: msg, bubble: bubble };
    }
    function renderSources(bubble, sources) {
      if (!sources || !sources.length) return;
      var box = h("div", "sources", "<h4>Sources</h4>");
      sources.forEach(function (s) {
        var a = h("a", "source");
        a.href = s.url || "#";
        if (s.url) a.target = "_top";
        a.innerHTML = '<span class="n">' + s.index + '</span><span class="t">' +
          escapeHtml(s.title || "Untitled") + "</span>";
        box.appendChild(a);
      });
      bubble.appendChild(box);
    }
    function wireCites(bubble, sources) {
      bubble.querySelectorAll(".cite").forEach(function (el) {
        el.onclick = function (e) {
          e.preventDefault();
          var n = parseInt(el.getAttribute("data-cite"), 10);
          var s = (sources || []).find(function (x) { return x.index === n; });
          if (s && s.url) window.top.location.href = s.url;
        };
      });
    }
    function submit() {
      var q = textarea.value.trim();
      if (!q || busy) return;
      textarea.value = ""; textarea.style.height = "auto";
      addMessage("user", q);
      ask(q);
    }
    function ask(question) {
      busy = true; sendBtn.disabled = true;
      var out = addMessage("assistant", "");
      var answer = "", sources = [], started = false;
      streamAsk(question, mode,
        function (ev) {
          if (ev.type === "sources") sources = ev.sources || [];
          else if (ev.type === "delta") {
            if (!started) { out.bubble.innerHTML = ""; started = true; }
            answer += ev.text || "";
            out.bubble.innerHTML = renderMarkdown(answer);
            wireCites(out.bubble, sources);
            thread.scrollTop = thread.scrollHeight;
          } else if (ev.type === "error") {
            out.bubble.innerHTML = renderMarkdown(answer ||
              "Sorry — I couldn't generate an answer. Please try again.");
          }
        },
        function () {
          if (!started) out.bubble.innerHTML = renderMarkdown(answer || "No answer.");
          renderSources(out.bubble, sources); wireCites(out.bubble, sources);
          busy = false; sendBtn.disabled = false; thread.scrollTop = thread.scrollHeight;
        },
        function () {
          out.bubble.innerHTML = renderMarkdown(
            "Sorry — the assistant is unavailable right now. Please try again later.");
          busy = false; sendBtn.disabled = false;
        });
    }
  }

  function streamAsk(question, mode, onEvent, onDone, onFail) {
    var headers = { "Content-Type": "application/json" };
    if (API_KEY) headers["Authorization"] = "Bearer " + API_KEY;
    fetch(CONFIG.endpoint.replace(/\/$/, "") + "/docs/ask", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ query: question, mode: mode }),
    })
      .then(function (resp) {
        if (!resp.ok || !resp.body) throw new Error("bad status " + resp.status);
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buf = "";
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) { if (buf.trim()) dispatchLine(buf, onEvent); onDone(); return; }
            buf += decoder.decode(r.value, { stream: true });
            var lines = buf.split("\n");
            buf = lines.pop();
            lines.forEach(function (l) { dispatchLine(l, onEvent); });
            return pump();
          });
        }
        return pump();
      })
      .catch(function () { onFail(); });
  }
  function dispatchLine(line, onEvent) {
    line = line.trim();
    if (!line) return;
    try { onEvent(JSON.parse(line)); } catch (e) {}
  }

  function mount() {
    if (document.getElementById(HOST_ID)) return;
    var host = document.createElement("div");
    host.id = HOST_ID;
    document.body.appendChild(host);
    AskAI(host.attachShadow({ mode: "open" }));
  }
  function ensure() {
    if (!document.body) document.addEventListener("DOMContentLoaded", mount);
    else mount();
  }

  // Hide the host site's own AI-assistant trigger (e.g. Mintlify's "Ask
  // Assistant ⌘I" button in the navbar), so there's a single Ask AI entry point
  // and Cmd/Ctrl+I is unambiguous. Text-based match, scoped away from our own
  // Shadow-DOM host; runs now and again whenever the host re-renders (SPA nav).
  function hideHostAssistant() {
    var nodes = document.querySelectorAll("button, a, [role=button]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && el.closest("#" + HOST_ID)) continue; // never our widget
      if (el.getAttribute("data-hydra-hidden") === "1") continue;
      var label = ((el.textContent || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase();
      // Match the built-in assistant, but not a plain "Ask AI" launcher clone.
      if (/\bask\s*assistant\b/.test(label) || (/\bassistant\b/.test(label) && /(⌘|ctrl).?\s*i\b/.test(label))) {
        el.setAttribute("data-hydra-hidden", "1");
        el.style.setProperty("display", "none", "important");
      }
    }
  }

  ensure();
  hideHostAssistant();
  // Host apps hydrate the navbar late and re-render on client-side navigation,
  // so keep both the widget and the hide-rule enforced.
  var mo = new MutationObserver(function () {
    if (!document.getElementById(HOST_ID)) ensure();
    hideHostAssistant();
  });
  if (document.body) mo.observe(document.body, { childList: true, subtree: true });
  // A few delayed passes cover the first hydration before the observer attaches.
  [400, 1200, 3000].forEach(function (t) { setTimeout(hideHostAssistant, t); });
})();
