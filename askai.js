/*
 * askai.js — HydraDB documentation "Ask AI" widget.
 *
 * Mintlify auto-executes root-level .js (the same mechanism reo.js and footer.js
 * rely on). This injects a self-contained Ask-AI launcher + slide-out panel and
 * re-mounts it on client-side navigation.
 *
 * The entire widget lives inside a Shadow DOM host, so Mintlify's Tailwind can't
 * leak in and our styles can't leak out — no CSS overrides, no !important wars
 * against the closed docs shell. It is public-safe by design: no model picker,
 * no Slack/Linear scopes, no reasoning control. It streams grounded, cited
 * answers from POST {ASKAI_ENDPOINT}/docs/ask as newline-delimited JSON.
 *
 * Config (window.ASKAI_CONFIG, set before this script or via a small inline
 * snippet): { endpoint, apiKey }. endpoint defaults to the production docs API.
 */
(function () {
  "use strict";

  var CONFIG = Object.assign(
    {
      endpoint: "https://agents.hydradb.com",
      apiKey: "",
      title: "Ask AI",
      placeholder: "Ask about HydraDB…",
      greeting:
        "Hi! I can answer questions about HydraDB using the documentation. What are you building?",
    },
    window.ASKAI_CONFIG || {}
  );

  var HOST_ID = "hydra-askai-root";

  // ---- styles (scoped to the shadow root) --------------------------------
  var STYLE = `
    :host { all: initial; }
    *, *::before, *::after { box-sizing: border-box; }
    .wrap {
      font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      --accent: #FF571A;
      --accent-soft: rgba(255,87,26,0.12);
      --bg: #0b0b10;
      --panel: #14141b;
      --line: rgba(255,255,255,0.10);
      --text: #ececf1;
      --muted: #9a9aa8;
    }
    .launcher {
      position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
      display: inline-flex; align-items: center; gap: 8px;
      padding: 11px 16px; border: none; border-radius: 999px; cursor: pointer;
      background: var(--accent); color: #fff; font-size: 14px; font-weight: 600;
      box-shadow: 0 6px 24px rgba(255,87,26,0.35);
      transition: transform .15s ease, box-shadow .15s ease;
    }
    .launcher:hover { transform: translateY(-1px); box-shadow: 0 10px 30px rgba(255,87,26,0.45); }
    .launcher svg { width: 17px; height: 17px; }

    .scrim {
      position: fixed; inset: 0; z-index: 2147483200; background: rgba(0,0,0,0.45);
      opacity: 0; pointer-events: none; transition: opacity .2s ease;
    }
    .scrim.open { opacity: 1; pointer-events: auto; }

    .panel {
      position: fixed; top: 0; right: 0; height: 100%; width: 460px; max-width: 100vw;
      z-index: 2147483201; background: var(--panel); color: var(--text);
      border-left: 1px solid var(--line); display: flex; flex-direction: column;
      transform: translateX(100%); transition: transform .24s cubic-bezier(.4,0,.2,1);
      box-shadow: -20px 0 60px rgba(0,0,0,0.4);
    }
    .panel.open { transform: translateX(0); }

    .head {
      display: flex; align-items: center; gap: 10px; padding: 16px 18px;
      border-bottom: 1px solid var(--line); flex: 0 0 auto;
    }
    .head .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent);
      box-shadow: 0 0 0 4px var(--accent-soft); }
    .head h2 { margin: 0; font-size: 15px; font-weight: 600; letter-spacing: .01em; }
    .head .spacer { flex: 1; }
    .head button.close {
      background: transparent; border: none; color: var(--muted); cursor: pointer;
      font-size: 20px; line-height: 1; padding: 4px 8px; border-radius: 8px;
    }
    .head button.close:hover { color: var(--text); background: rgba(255,255,255,0.06); }

    .thread { flex: 1 1 auto; overflow-y: auto; padding: 18px; display: flex;
      flex-direction: column; gap: 16px; }
    .msg { display: flex; flex-direction: column; gap: 6px; max-width: 100%; }
    .msg .role { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
      color: var(--muted); }
    .bubble { font-size: 14px; line-height: 1.6; }
    .msg.user .bubble {
      align-self: flex-end; background: var(--accent-soft); border: 1px solid rgba(255,87,26,0.25);
      color: var(--text); padding: 9px 13px; border-radius: 12px 12px 2px 12px; max-width: 85%;
    }
    .msg.user { align-items: flex-end; }
    .bubble p { margin: 0 0 10px; }
    .bubble p:last-child { margin-bottom: 0; }
    .bubble pre { background: #0b0b10; border: 1px solid var(--line); border-radius: 8px;
      padding: 12px; overflow-x: auto; font-size: 12.5px; }
    .bubble code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(255,255,255,0.07); padding: 1px 5px; border-radius: 5px; font-size: 12.5px; }
    .bubble pre code { background: none; padding: 0; }
    .cite {
      display: inline-flex; align-items: center; justify-content: center;
      min-width: 17px; height: 17px; padding: 0 4px; margin: 0 1px; vertical-align: baseline;
      font-size: 10px; font-weight: 700; color: var(--accent);
      background: var(--accent-soft); border-radius: 5px; cursor: pointer; text-decoration: none;
    }

    .dots { display: inline-flex; gap: 4px; align-items: center; height: 18px; }
    .dots span { width: 6px; height: 6px; border-radius: 50%; background: var(--muted);
      animation: blink 1.2s infinite ease-in-out both; }
    .dots span:nth-child(2) { animation-delay: .2s; }
    .dots span:nth-child(3) { animation-delay: .4s; }
    @keyframes blink { 0%,80%,100% { opacity: .25; } 40% { opacity: 1; } }

    .sources { margin-top: 10px; border-top: 1px solid var(--line); padding-top: 10px; }
    .sources h4 { margin: 0 0 8px; font-size: 11px; text-transform: uppercase;
      letter-spacing: .08em; color: var(--muted); }
    .source {
      display: flex; gap: 8px; align-items: baseline; padding: 6px 8px; border-radius: 8px;
      text-decoration: none; color: var(--text);
    }
    .source:hover { background: rgba(255,255,255,0.05); }
    .source .n { color: var(--accent); font-weight: 700; font-size: 11px; min-width: 16px; }
    .source .t { font-size: 13px; }

    .composer { flex: 0 0 auto; border-top: 1px solid var(--line); padding: 12px;
      display: flex; gap: 8px; align-items: flex-end; }
    .composer textarea {
      flex: 1; resize: none; max-height: 120px; min-height: 42px; padding: 10px 12px;
      background: var(--bg); border: 1px solid var(--line); border-radius: 10px;
      color: var(--text); font: inherit; font-size: 14px; line-height: 1.4; outline: none;
    }
    .composer textarea:focus { border-color: rgba(255,87,26,0.5); }
    .composer button.send {
      flex: 0 0 auto; width: 42px; height: 42px; border-radius: 10px; border: none; cursor: pointer;
      background: var(--accent); color: #fff; display: inline-flex; align-items: center; justify-content: center;
    }
    .composer button.send:disabled { opacity: .45; cursor: default; }
    .composer button.send svg { width: 18px; height: 18px; }
    .foot { padding: 0 12px 10px; font-size: 11px; color: var(--muted); text-align: center; }

    @media (max-width: 520px) { .panel { width: 100vw; } .launcher { right: 16px; bottom: 16px; } }
  `;

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
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // Minimal, safe markdown: escape first, then render fenced code, inline code,
  // bold, bullet lists, [n] citations, and paragraphs. No raw HTML passes through.
  function renderMarkdown(src) {
    var out = escapeHtml(src);
    out = out.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
      return "<pre><code>" + code.replace(/\n$/, "") + "</code></pre>";
    });
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/\[(\d+)\]/g, function (_, n) {
      return '<a class="cite" data-cite="' + n + '" href="#">' + n + "</a>";
    });
    // paragraphs / line breaks
    var blocks = out.split(/\n{2,}/).map(function (b) {
      if (/^<pre>/.test(b.trim())) return b;
      return "<p>" + b.trim().replace(/\n/g, "<br>") + "</p>";
    });
    return blocks.join("");
  }

  function AskAI(shadow) {
    var busy = false;
    var wrap = h("div", "wrap");

    var launcher = h(
      "button",
      "launcher",
      ICON_SPARK + "<span>" + escapeHtml(CONFIG.title) + "</span>"
    );
    launcher.setAttribute("aria-label", "Open " + CONFIG.title);

    var scrim = h("div", "scrim");
    var panel = h("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", CONFIG.title);

    var head = h(
      "div",
      "head",
      '<span class="dot"></span><h2>' +
        escapeHtml(CONFIG.title) +
        '</h2><span class="spacer"></span>'
    );
    var closeBtn = h("button", "close", "&times;");
    closeBtn.setAttribute("aria-label", "Close");
    head.appendChild(closeBtn);

    var thread = h("div", "thread");
    var composer = h("div", "composer");
    var textarea = h("textarea");
    textarea.placeholder = CONFIG.placeholder;
    textarea.rows = 1;
    var sendBtn = h("button", "send", ICON_SEND);
    sendBtn.setAttribute("aria-label", "Send");
    composer.appendChild(textarea);
    composer.appendChild(sendBtn);
    var foot = h("div", "foot", "Answers are AI-generated from the docs. Verify before relying on them.");

    panel.appendChild(head);
    panel.appendChild(thread);
    panel.appendChild(composer);
    panel.appendChild(foot);

    wrap.appendChild(launcher);
    wrap.appendChild(scrim);
    wrap.appendChild(panel);

    var styleEl = h("style");
    styleEl.textContent = STYLE;
    shadow.appendChild(styleEl);
    shadow.appendChild(wrap);

    // greeting
    addMessage("assistant", CONFIG.greeting);

    function open() {
      scrim.classList.add("open");
      panel.classList.add("open");
      setTimeout(function () { textarea.focus(); }, 60);
    }
    function close() {
      scrim.classList.remove("open");
      panel.classList.remove("open");
    }
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
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submit();
      }
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
        a.innerHTML =
          '<span class="n">' + s.index + '</span><span class="t">' +
          escapeHtml(s.title || "Untitled") + "</span>";
        box.appendChild(a);
      });
      bubble.appendChild(box);
    }

    function submit() {
      var q = textarea.value.trim();
      if (!q || busy) return;
      textarea.value = "";
      textarea.style.height = "auto";
      addMessage("user", q);
      ask(q);
    }

    function ask(question) {
      busy = true;
      sendBtn.disabled = true;
      var out = addMessage("assistant", "");
      var answer = "";
      var sources = [];
      var started = false;

      streamAsk(
        question,
        function onEvent(ev) {
          if (ev.type === "sources") {
            sources = ev.sources || [];
          } else if (ev.type === "delta") {
            if (!started) { out.bubble.innerHTML = ""; started = true; }
            answer += ev.text || "";
            out.bubble.innerHTML = renderMarkdown(answer);
            wireCites(out.bubble, sources);
            thread.scrollTop = thread.scrollHeight;
          } else if (ev.type === "error") {
            out.bubble.innerHTML = renderMarkdown(
              answer || "Sorry — I couldn't generate an answer just now. Please try again."
            );
          }
        },
        function onDone() {
          if (!started) out.bubble.innerHTML = renderMarkdown(answer || "No answer.");
          renderSources(out.bubble, sources);
          wireCites(out.bubble, sources);
          busy = false;
          sendBtn.disabled = false;
          thread.scrollTop = thread.scrollHeight;
        },
        function onFail() {
          out.bubble.innerHTML = renderMarkdown(
            "Sorry — the assistant is unavailable right now. Please try again later."
          );
          busy = false;
          sendBtn.disabled = false;
        }
      );
    }

    function wireCites(bubble, sources) {
      bubble.querySelectorAll(".cite").forEach(function (el) {
        el.onclick = function (e) {
          e.preventDefault();
          var n = parseInt(el.getAttribute("data-cite"), 10);
          var s = sources.find(function (x) { return x.index === n; });
          if (s && s.url) window.top.location.href = s.url;
        };
      });
    }
  }

  // streamAsk POSTs the question and parses the NDJSON event stream.
  function streamAsk(question, onEvent, onDone, onFail) {
    var headers = { "Content-Type": "application/json" };
    if (CONFIG.apiKey) headers["Authorization"] = "Bearer " + CONFIG.apiKey;

    fetch(CONFIG.endpoint.replace(/\/$/, "") + "/docs/ask", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ query: question }),
    })
      .then(function (resp) {
        if (!resp.ok || !resp.body) throw new Error("bad status " + resp.status);
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buf = "";
        function pump() {
          return reader.read().then(function (r) {
            if (r.done) {
              if (buf.trim()) dispatchLine(buf, onEvent);
              onDone();
              return;
            }
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
    try { onEvent(JSON.parse(line)); } catch (e) { /* ignore partial */ }
  }

  // ---- mount / re-mount on SPA navigation --------------------------------
  function mount() {
    if (document.getElementById(HOST_ID)) return;
    var host = document.createElement("div");
    host.id = HOST_ID;
    document.body.appendChild(host);
    var shadow = host.attachShadow({ mode: "open" });
    AskAI(shadow);
  }

  function ensure() {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", mount);
    } else {
      mount();
    }
  }
  ensure();
  // Mintlify is a SPA; re-mount if the host is ever torn out on navigation.
  var mo = new MutationObserver(function () {
    if (!document.getElementById(HOST_ID)) ensure();
  });
  if (document.body) mo.observe(document.body, { childList: true });
})();
