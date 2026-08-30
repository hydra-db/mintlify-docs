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
 * ── Configure ──────────────────────────────────────────────────────────────
 * Set window.HydraAskAI (or legacy window.ASKAI_CONFIG) BEFORE this script:
 *
 *   window.HydraAskAI = {
 *     endpoint: "https://agents.hydradb.com", // ask API base
 *     apiKey:   "pk_docs_readonly_...",        // optional, see "API key" below
 *     theme:    { accent: "#FF571A" },          // optional brand override
 *     modes:    ["fast", "auto", "thinking"],  // which think-modes to show
 *     brand:    true,                            // "Powered by HydraDB" footer
 *   };
 *
 * ── API key (composable, no prop required) ─────────────────────────────────
 * Resolution order: config.apiKey → window.HYDRA_ASKAI_KEY → none. In a build
 * step (Next.js/Vite/Docusaurus) inject a PUBLIC, read-only, docs-scoped key:
 *   window.HYDRA_ASKAI_KEY = process.env.NEXT_PUBLIC_HYDRA_ASKAI_KEY
 * If your endpoint injects the key server-side, omit it entirely.
 *
 * ── Theme (takes the host's colors) ────────────────────────────────────────
 * Precedence: config.theme.<token> → host CSS var --askai-<token> on :root →
 * built-in default. So a host that sets `--askai-accent:#7C3AED` is themed with
 * zero JS. Tokens: accent, panel, bg, text, muted, line.
 */
(function () {
  "use strict";

  var cfg = window.HydraAskAI || window.ASKAI_CONFIG || {};
  var CONFIG = Object.assign(
    {
      endpoint: "https://agents.hydradb.com",
      apiKey: "",
      title: "Ask AI",
      placeholder: "Ask about the docs…",
      greeting:
        "Hi! I can answer questions from the documentation, with sources. What are you looking for?",
      modes: ["fast", "auto", "thinking"],
      defaultMode: "auto",
      brand: true,
      brandUrl: "https://hydradb.com",
      position: "right", // reserved
      theme: {},
    },
    cfg
  );

  // API key: config → env-injected global → none.
  var API_KEY = CONFIG.apiKey || window.HYDRA_ASKAI_KEY || "";

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
      display: inline-flex; align-items: center; gap: 8px;
      padding: 11px 16px; border: none; border-radius: 999px; cursor: pointer;
      background: var(--accent); color: #fff; font-size: 14px; font-weight: 600;
      box-shadow: 0 6px 24px var(--accent-line);
      transition: transform .15s ease, box-shadow .15s ease;
    }
    .launcher:hover { transform: translateY(-1px); }
    .launcher svg { width: 17px; height: 17px; }

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
    .head .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent);
      box-shadow: 0 0 0 4px var(--accent-soft); }
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

    .foot { padding: 0 12px 10px; display: flex; align-items: center; justify-content: center; gap: 6px;
      font-size: 11px; color: var(--muted); flex: 0 0 auto; }
    .foot .brand { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); text-decoration: none; font-weight: 600; }
    .foot .brand:hover { color: var(--text); }
    .foot .brand .mk { width: 12px; height: 12px; border-radius: 3px; background: var(--accent); display: inline-block; }

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

  var ICON_SPARK =
    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.9 5.1L19 9l-5.1 1.9L12 16l-1.9-5.1L5 9l5.1-1.9z"/></svg>';
  var ICON_SEND =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/></svg>';

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
    var launcher = h("button", "launcher", ICON_SPARK + "<span>" + escapeHtml(CONFIG.title) + "</span>");
    launcher.setAttribute("aria-label", "Open " + CONFIG.title);
    var scrim = h("div", "scrim");
    var panel = h("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", CONFIG.title);

    var head = h("div", "head",
      '<span class="dot"></span><h2>' + escapeHtml(CONFIG.title) + '</h2><span class="spacer"></span>');
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

    var brandHtml = CONFIG.brand
      ? '<a class="brand" href="' + escapeHtml(CONFIG.brandUrl) + '" target="_blank" rel="noopener">' +
        '<span class="mk"></span>Powered by HydraDB</a>'
      : "Answers are AI-generated — verify before relying on them.";
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

    addMessage("assistant", CONFIG.greeting);

    function open() {
      scrim.classList.add("open");
      panel.classList.add("open");
      setTimeout(function () { textarea.focus(); }, 60);
    }
    function close() { scrim.classList.remove("open"); panel.classList.remove("open"); }
    launcher.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    scrim.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "i") {
        e.preventDefault();
        panel.classList.contains("open") ? close() : open();
      }
    });
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
  ensure();
  var mo = new MutationObserver(function () {
    if (!document.getElementById(HOST_ID)) ensure();
  });
  if (document.body) mo.observe(document.body, { childList: true });
})();
