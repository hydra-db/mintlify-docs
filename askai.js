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
  // │   • Staging:     "https://askai.staging.hydradb.com"                 │
  // │   • Production:  "https://ask.yourdomain.com"                        │
  // │ (You can still override at runtime with window.HydraAskAI.endpoint.) │
  // └─────────────────────────────────────────────────────────────────────┘
  var DEFAULT_ENDPOINT = "https://askai.staging.hydradb.com";

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
      // Think-modes are disabled for now: a single "auto" mode hides the
      // segmented control (it only renders when modes.length > 1) and every
      // question is asked in auto. Restore ["fast","auto","thinking"] to re-enable.
      modes: ["auto"],
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
  // Touch-first / small screens have no usable physical-keyboard shortcut, so we
  // hide the ⌘I/Ctrl+I launcher badge (CSS below) and drop the tip from the
  // greeting there. We treat a device as "mobile" if ANY of these hold, so the
  // greeting stays consistent with the badge even when a browser doesn't report
  // `pointer: coarse`: coarse pointer, a narrow viewport (≤520px), or a touch
  // digitizer. (Matches the launcher-kbd media query below.)
  function mq(q) { return !!(window.matchMedia && window.matchMedia(q).matches); }
  var IS_TOUCH =
    mq("(pointer: coarse)") ||
    mq("(max-width: 520px)") ||
    "ontouchstart" in window ||
    (navigator.maxTouchPoints || 0) > 0;

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
    /* Hide the ⌘I/Ctrl+I badge where it can't be used: narrow screens and any
       touch-first (coarse-pointer) device — phones and most tablets. */
    @media (max-width: 520px), (pointer: coarse) { .launcher-kbd { display: none; } }
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
      width: 440px; max-width: 100%; z-index: 2147483201;
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
    .bubble h1, .bubble h2, .bubble h3, .bubble h4, .bubble h5, .bubble h6 {
      margin: 14px 0 6px; line-height: 1.3; font-weight: 700; }
    .bubble h1 { font-size: 18px; } .bubble h2 { font-size: 16px; }
    .bubble h3 { font-size: 14.5px; } .bubble h4, .bubble h5, .bubble h6 { font-size: 13px; }
    .bubble > :first-child { margin-top: 0; }
    .bubble a:not(.cite) { color: var(--accent); text-decoration: none; }
    .bubble a:not(.cite):hover { text-decoration: underline; }
    .bubble em { font-style: italic; } .bubble del { opacity: .6; }
    .bubble ol { margin: 0 0 10px; padding-left: 22px; } .bubble ol li { margin: 2px 0; }
    .bubble blockquote { margin: 0 0 10px; padding: 3px 0 3px 12px; color: var(--muted);
      border-left: 3px solid var(--accent-line); }
    .bubble hr { border: none; border-top: 1px solid var(--line); margin: 12px 0; }
    .bubble .table-wrap { overflow-x: auto; margin: 0 0 10px; }
    .bubble table { border-collapse: collapse; width: 100%; font-size: 13px; }
    .bubble th, .bubble td { border: 1px solid var(--line); padding: 6px 9px; text-align: left; vertical-align: top; }
    .bubble th { background: rgba(255,255,255,0.05); font-weight: 600; }
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
      .panel { width: 100%; border-left: none; }
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
    '<path d="M0 0 C528 0 1056 0 1600 0 C1600 528 1600 1056 1600 1600 C1072 1600 544 1600 0 1600 C0 1072 0 544 0 0 Z " fill="#000000" transform="translate(0,0)"/> <path d="M0 0 C97.68 0 195.36 0 296 0 C296.20165675 109.74160081 296.20165675 109.74160081 296.24414062 155.63867188 C296.25330628 165.47470138 296.26267704 175.31073065 296.27262997 185.1467594 C296.27465099 187.14496785 296.27665273 189.14317631 296.2786544 191.14138478 C296.30036806 212.36151621 296.33976893 233.58157951 296.38576398 254.80167062 C296.43261333 276.59858663 296.46031329 298.3954671 296.47044247 320.19243139 C296.47728406 333.6293751 296.498849 347.06612627 296.53958233 360.50301068 C296.56612545 369.73214366 296.57397492 378.96117792 296.56750689 388.19034625 C296.56432195 393.50560182 296.56901113 398.82052226 296.59602737 404.13571739 C296.72197532 430.21332896 296.61073485 454.99044415 285 479 C284.63745117 479.75265137 284.27490234 480.50530273 283.90136719 481.28076172 C276.29914697 496.82508813 266.90569861 509.50422303 255 522 C254.26007812 522.804375 253.52015625 523.60875 252.7578125 524.4375 C241.34430134 536.47812716 225.38114034 545.88337348 210 552 C208.94683594 552.42152344 207.89367188 552.84304688 206.80859375 553.27734375 C180.45108323 563.62244173 152.95685169 562.43135086 125.08553672 562.34819686 C119.56852532 562.33510141 114.05152272 562.33963539 108.53450012 562.34190369 C99.00267996 562.34346798 89.47092196 562.33414772 79.93911743 562.31719017 C66.15815841 562.29268928 52.37722909 562.28498362 38.59625065 562.2812262 C16.23130608 562.274679 -6.13360957 562.25479113 -28.49853516 562.22631836 C-50.20914545 562.19871074 -71.91974679 562.17753741 -93.63037109 562.16479492 C-94.99851445 562.163983 -96.36665781 562.16317088 -97.73480117 562.16235858 C-108.32939449 562.15610079 -118.92398803 562.15024895 -129.51858163 562.14449489 C-181.34575142 562.1162527 -233.17283839 562.05588392 -285 562 C-285 477.19 -285 392.38 -285 305 C-157.25 304.8125 -157.25 304.8125 -116.92285156 304.79223633 C-104.98653207 304.76235823 -104.98653207 304.76235823 -93.05023193 304.72587585 C-87.73652789 304.70981192 -82.42302221 304.70727552 -77.10931396 304.71403503 C-70.32558128 304.7226125 -63.542309 304.70539481 -56.75865507 304.67198312 C-54.27265739 304.66377654 -51.78661457 304.6638871 -49.30062151 304.67339098 C-31.93481437 305.07749966 -31.93481437 305.07749966 -16 299 C-15.10199865 298.44778385 -15.10199865 298.44778385 -14.18585587 297.88441181 C-7.56717844 293.07403436 -3.08971248 284.75575635 -1 277 C-0.57823269 273.17082916 -0.5835689 269.37148033 -0.60127258 265.52197266 C-0.59131004 263.81910347 -0.59131004 263.81910347 -0.58114624 262.08183289 C-0.56249859 258.27581837 -0.56514355 254.47011765 -0.56762695 250.6640625 C-0.56017574 247.88966606 -0.54824034 245.11529301 -0.53649426 242.34091187 C-0.51082299 235.66674911 -0.50041987 228.99265042 -0.49541168 222.31844366 C-0.48895335 214.59505069 -0.4634027 206.87179785 -0.43786621 199.1484375 C-0.37987037 178.76566467 -0.34776638 158.38282483 -0.3125 138 C-0.209375 92.46 -0.10625 46.92 0 0 Z " fill="#FE5618" transform="translate(1085,238)"/> <path d="M0 0 C1.90210498 -0.00087384 3.80420972 -0.00262809 5.70631319 -0.00517833 C10.8943751 -0.00962084 16.08235342 -0.0016911 21.2704047 0.00837779 C26.87960027 0.01705494 32.48879154 0.01408912 38.09799194 0.01257324 C47.80347742 0.01152708 57.50893591 0.01777554 67.2144146 0.02904892 C81.24671931 0.04533746 95.27901104 0.05051536 109.31132419 0.0530249 C132.08056944 0.05739675 154.84979714 0.07067473 177.61903381 0.08963013 C199.73068672 0.10801676 221.84233575 0.12214143 243.95399475 0.13064575 C245.34591033 0.13118704 246.73782591 0.13172844 248.12974149 0.13226998 C258.90737733 0.13644147 269.68501326 0.14034284 280.46264923 0.14417911 C333.2446897 0.16302946 386.02669418 0.20327011 438.80873108 0.24050903 C438.80873108 84.72050903 438.80873108 169.20050903 438.80873108 256.24050903 C311.43373108 256.49050903 311.43373108 256.49050903 271.20960999 256.5383606 C259.31450588 256.57157903 259.31450588 256.57157903 247.41941833 256.6100769 C242.11982406 256.62708716 236.82034805 256.63326929 231.52073669 256.63204956 C224.75856068 256.63053469 217.99673353 256.64956685 211.23461986 256.68159819 C208.75429635 256.69011259 206.27393874 256.69195746 203.793607 256.68633318 C200.43872126 256.67968967 197.08481353 256.69760381 193.73002625 256.7215271 C192.76335211 256.71376629 191.79667797 256.70600548 190.80071068 256.69800949 C180.76041994 256.82404967 172.16136373 260.37601797 164.80873108 267.24050903 C156.37664611 276.21441472 154.35422421 284.74355645 154.41000366 296.83879089 C154.40336197 297.97489304 154.39672028 299.11099518 154.38987732 300.28152466 C154.37125298 304.08443181 154.3738731 307.88702425 154.37635803 311.68997192 C154.36890418 314.4644901 154.35696947 317.23898492 154.34522533 320.01348782 C154.31956958 326.68415015 154.30915314 333.35474839 154.30414275 340.0254547 C154.29768084 347.74591932 154.27212671 355.46624402 154.24659729 363.18667603 C154.18861697 383.55874738 154.15650126 403.9308857 154.12123108 424.30300903 C154.01810608 469.82238403 153.91498108 515.34175903 153.80873108 562.24050903 C56.12873108 562.24050903 -41.55126892 562.24050903 -142.19126892 562.24050903 C-142.31226297 453.09043468 -142.31226297 453.09043468 -142.3377533 407.47293091 C-142.34362659 397.03209418 -142.34968794 386.59125758 -142.35606384 376.15042114 C-142.35685509 374.84189885 -142.35764634 373.53337655 -142.35846156 372.18520206 C-142.37149638 351.0820028 -142.39514062 329.97882814 -142.42272731 308.87564345 C-142.45081836 287.20530544 -142.467453 265.53498027 -142.47353441 243.8646248 C-142.47764317 230.50142581 -142.49060319 217.13829638 -142.51501832 203.77511881 C-142.5309212 194.60162709 -142.535659 185.42817114 -142.53177305 176.25466665 C-142.52985859 170.9683446 -142.5327169 165.68214379 -142.54888535 160.39584351 C-142.56361682 155.5435933 -142.56413651 150.69154445 -142.55396561 145.83928446 C-142.55271086 144.09601981 -142.55640335 142.35274195 -142.56591847 140.60950281 C-142.64528145 125.15438613 -141.33527968 109.55223546 -136.19126892 94.86550903 C-135.89784607 94.01029663 -135.60442322 93.15508423 -135.30210876 92.2739563 C-124.45307511 62.08546057 -103.91076023 37.66040437 -77.19126892 20.24050903 C-76.40622986 19.70812622 -75.6211908 19.17574341 -74.81236267 18.62722778 C-52.11126826 3.98008687 -26.44706708 -0.09126187 0 0 Z " fill="#FE5618" transform="translate(361.19126892089844,799.7594909667969)"/> <path d="M0 0 C97.68 0 195.36 0 296 0 C296 127.71 296 255.42 296 387 C198.32 387 100.64 387 0 387 C0 259.29 0 131.58 0 0 Z " fill="#FE5618" transform="translate(219,238)"/> <path d="M0 0 C97.68 0 195.36 0 296 0 C296 121.77 296 243.54 296 369 C198.32 369 100.64 369 0 369 C0 247.23 0 125.46 0 0 Z " fill="#FD5618" transform="translate(1085,993)"/>' +
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
  // ── Markdown → safe HTML ────────────────────────────────────────────────────
  // LLMs answer in Markdown, so the widget parses a broad subset: headings,
  // bold/italic/strikethrough, inline + fenced code, links, ordered/unordered
  // lists, blockquotes, GFM tables, horizontal rules, and [n] citations.
  //
  // Safety: the source is HTML-escaped FIRST and we only ever emit tags we
  // generate ourselves — never markup copied from the source — so a model that
  // returns "<script>…" renders it as visible text, not live HTML.

  // Inline spans, applied to already-escaped text.
  function renderInline(s) {
    // Protect inline code so other rules don't touch its contents.
    var tokens = [];
    var stash = function (html) {
      tokens.push(html);
      return "@@" + (tokens.length - 1) + "@@";
    };
    s = s.replace(/`([^`]+)`/g, function (_, c) { return stash("<code>" + c + "</code>"); });
    // Links [text](url) — only http(s) or root-relative targets.
    s = s.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]*)\)/g,
      function (_, t, u) {
        return stash('<a href="' + u + '" target="_blank" rel="noopener">' + t + "</a>");
      }
    );
    // Bare [n] citations → clickable chips wired to the sources list.
    s = s.replace(/\[(\d+)\]/g, function (_, n) {
      return stash('<a class="cite" data-cite="' + n + '" href="#">' + n + "</a>");
    });
    // Bold before italic so ** isn't consumed by the * rule.
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^\w])_(?!\s)([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    // Restore stashed spans.
    s = s.replace(/@@(\d+)@@/g, function (_, i) { return tokens[i]; });
    return s;
  }

  function renderTable(rows) {
    var cells = function (r) {
      return r
        .replace(/^\s*\|/, "")
        .replace(/\|\s*$/, "")
        .split("|")
        .map(function (c) { return c.trim(); });
    };
    var header = cells(rows[0]);
    var thead =
      "<thead><tr>" +
      header.map(function (c) { return "<th>" + renderInline(c) + "</th>"; }).join("") +
      "</tr></thead>";
    var tbody =
      "<tbody>" +
      rows.slice(2).map(function (r) {
        var row = cells(r);
        return (
          "<tr>" +
          header
            .map(function (_, k) { return "<td>" + renderInline(row[k] || "") + "</td>"; })
            .join("") +
          "</tr>"
        );
      }).join("") +
      "</tbody>";
    return '<div class="table-wrap"><table>' + thead + tbody + "</table></div>";
  }

  var LIST_RE = /^\s*(\d+[.)]|[-*+])\s+/;

  function renderMarkdown(src) {
    var lines = escapeHtml(src).replace(/\r\n?/g, "\n").split("\n");
    var html = [];
    var para = [];
    function flushPara() {
      if (para.length) { html.push("<p>" + renderInline(para.join(" ")) + "</p>"); para = []; }
    }
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];

      // Fenced code block (```): verbatim, no inline formatting.
      if (/^\s*```/.test(line)) {
        flushPara();
        var code = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
        i++; // consume the closing fence (or run past EOF while streaming)
        html.push("<pre><code>" + code.join("\n") + "</code></pre>");
        continue;
      }
      // Blank line ends a paragraph.
      if (/^\s*$/.test(line)) { flushPara(); i++; continue; }
      // ATX heading.
      var head = line.match(/^(#{1,6})\s+(.*)$/);
      if (head) {
        flushPara();
        var lvl = head[1].length;
        html.push("<h" + lvl + ">" + renderInline(head[2].trim()) + "</h" + lvl + ">");
        i++; continue;
      }
      // Horizontal rule (---, ***, ___).
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushPara(); html.push("<hr>"); i++; continue; }
      // GFM table: header row followed by a |---|:--:| delimiter row.
      if (
        /\|/.test(line) &&
        i + 1 < lines.length &&
        /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/.test(lines[i + 1])
      ) {
        flushPara();
        var rows = [line, lines[i + 1]];
        i += 2;
        while (i < lines.length && /\|/.test(lines[i]) && !/^\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
        html.push(renderTable(rows));
        continue;
      }
      // Blockquote. Note: the source was HTML-escaped first, so ">" is "&gt;".
      if (/^\s*&gt;\s?/.test(line)) {
        flushPara();
        var quote = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) { quote.push(lines[i].replace(/^\s*&gt;\s?/, "")); i++; }
        html.push("<blockquote>" + renderInline(quote.join(" ")) + "</blockquote>");
        continue;
      }
      // Ordered / unordered list (one level, with wrapped-line continuation).
      if (LIST_RE.test(line)) {
        flushPara();
        var ordered = /^\s*\d+[.)]\s+/.test(line);
        var items = [];
        while (i < lines.length && LIST_RE.test(lines[i])) {
          items.push(lines[i].replace(LIST_RE, ""));
          i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !LIST_RE.test(lines[i])) {
            items[items.length - 1] += " " + lines[i].trim();
            i++;
          }
        }
        var tag = ordered ? "ol" : "ul";
        html.push(
          "<" + tag + ">" +
          items.map(function (it) { return "<li>" + renderInline(it) + "</li>"; }).join("") +
          "</" + tag + ">"
        );
        continue;
      }
      // Plain paragraph text (soft-wrapped lines join with a space).
      para.push(line.trim());
      i++;
    }
    flushPara();
    return html.join("");
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

    addMessage(
      "assistant",
      CONFIG.greeting +
        (IS_TOUCH
          ? ""
          : "\n\nTip: press **" + SHORTCUT + "** anytime to open or close Ask AI.")
    );

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

  // Hide the host site's own AI-assistant surfaces so there's a single Ask AI
  // entry point (ours) and Cmd/Ctrl+I is unambiguous. Covers Mintlify's navbar
  // "Ask Assistant ⌘I" button, its floating "Ask a question…" input, and the
  // "Add to assistant" tooltip that pops up when you select text on the page.
  // Scoped away from our own Shadow-DOM host; re-runs on host re-render (SPA nav)
  // and on text selection (the tooltip is created on demand).

  // Known host-assistant containers, matched by stable selector. Each match is
  // hidden along with a sticky/fixed wrapper ancestor (so no empty bar is left).
  var HOST_ASSISTANT_SELECTORS = [
    "#assistant-entry",               // Mintlify navbar "Ask Assistant ⌘I" button
    "#text-selection-tooltip-button", // Mintlify "Add to assistant" selection popover
    ".chat-assistant-floating-input", // Mintlify floating "Ask a question…" box
    "#chat-assistant-textarea",
    "[id^='chat-assistant']",
    "[class*='chat-assistant-sheet']", // the assistant panel itself, if it opens
  ];

  function hide(el) {
    if (!el || (el.closest && el.closest("#" + HOST_ID))) return; // never our widget
    if (el.getAttribute("data-hydra-hidden") === "1") return;
    el.setAttribute("data-hydra-hidden", "1");
    el.style.setProperty("display", "none", "important");
  }

  function hideHostAssistant() {
    // 1. Selector-based surfaces (the floating input). Also climb to a sticky/
    //    fixed wrapper within a few levels so its reserved space collapses too.
    HOST_ASSISTANT_SELECTORS.forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        var target = el;
        for (var up = 0; up < 4 && target.parentElement; up++) {
          var pos = "";
          try { pos = getComputedStyle(target.parentElement).position; } catch (e) {}
          if (pos === "sticky" || pos === "fixed") { target = target.parentElement; break; }
          target = target.parentElement;
        }
        hide(target === el ? el : target);
        hide(el);
      });
    });
    // 2. Text-based trigger (the navbar "Ask Assistant ⌘I" button).
    var nodes = document.querySelectorAll("button, a, [role=button]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && el.closest("#" + HOST_ID)) continue;
      if (el.getAttribute("data-hydra-hidden") === "1") continue;
      var label = ((el.textContent || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase();
      if (
        /\bask\s*assistant\b/.test(label) ||
        /\badd to assistant\b/.test(label) ||
        (/\bassistant\b/.test(label) && /(⌘|ctrl).?\s*i\b/.test(label))
      ) {
        hide(el);
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
  // The "Add to assistant" tooltip is created on text selection — the observer
  // catches the DOM insert, but listen on selection too so it's squashed the
  // instant it would appear (before it can paint), on desktop and on mobile.
  ["selectionchange", "mouseup", "touchend", "keyup"].forEach(function (evt) {
    document.addEventListener(evt, function () {
      hideHostAssistant();
      // one more on the next frame, after the host positions/shows the tooltip
      setTimeout(hideHostAssistant, 0);
    }, true);
  });
})();
