const SEED_KNOWLEDGE = [
  { id: 'k1', text: 'Our refund policy is 30 days from purchase, no questions asked.' },
  { id: 'k2', text: 'The API rate limit is 100 requests per minute per API key.' },
]

const SEED_MEMORY = [
  { id: 'm1', owner: 'alice', infer: true, raw: 'I really like dark mode and minimal UI.', text: 'Likes dark mode and minimal UI.' },
  { id: 'm2', owner: 'bob', infer: false, raw: "User's plan tier is Pro.", text: "User's plan tier is Pro." },
]

function personaName(key) {
  return key === 'alice' ? 'Alice' : 'Bob'
}

function simulateInfer(raw) {
  const lower = raw.toLowerCase()
  const patterns = [
    { re: /prefers?\s+([^.,;]+)/, label: 'Prefers' },
    { re: /likes?\s+([^.,;]+)/, label: 'Likes' },
    { re: /hates?\s+([^.,;]+)/, label: 'Dislikes' },
    { re: /works?\s+in\s+([^.,;]+)/, label: 'Works in' },
  ]
  for (const p of patterns) {
    const m = lower.match(p.re)
    if (m) {
      const captured = m[1].trim().replace(/\.$/, '')
      return `${p.label} ${captured}.`
    }
  }
  return raw.length > 64 ? raw.slice(0, 61) + '…' : raw
}

export const KnowledgeMemoryPlayground = () => {
  const [persona, setPersona] = useState('alice')
  const [typeFilter, setTypeFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [knowledge, setKnowledge] = useState(SEED_KNOWLEDGE)
  const [memory, setMemory] = useState(SEED_MEMORY)

  const [knowledgeDraft, setKnowledgeDraft] = useState('')
  const [memoryDraft, setMemoryDraft] = useState('')
  const [memoryOwnerDraft, setMemoryOwnerDraft] = useState('alice')
  const [inferDraft, setInferDraft] = useState(true)

  const matches = (text, q) => !q || text.toLowerCase().includes(q.toLowerCase())

  const results = []
  if (typeFilter === 'knowledge' || typeFilter === 'all') {
    knowledge.forEach((item) => {
      if (matches(item.text, query)) {
        results.push({ id: item.id, kind: 'knowledge', text: item.text, scope: 'shared · everyone' })
      }
    })
  }
  if (typeFilter === 'memory' || typeFilter === 'all') {
    memory.forEach((item) => {
      if (item.owner !== persona) return
      if (matches(item.text, query)) {
        results.push({
          id: item.id,
          kind: 'memory',
          text: item.text,
          scope: `private · ${personaName(item.owner)} only`,
          raw: item.infer ? item.raw : null,
        })
      }
    })
  }

  const storeLabel = typeFilter === 'all' ? 'knowledge + memory' : typeFilter
  const metaText = `${results.length} result${results.length === 1 ? '' : 's'} · ${storeLabel} · viewing as ${personaName(persona)}${query ? ` · “${query}”` : ''}`

  const addKnowledge = () => {
    const val = knowledgeDraft.trim()
    if (!val) return
    setKnowledge((prev) => [...prev, { id: `k${Date.now()}`, text: val }])
    setKnowledgeDraft('')
  }

  const addMemory = () => {
    const val = memoryDraft.trim()
    if (!val) return
    const text = inferDraft ? simulateInfer(val) : val
    setMemory((prev) => [...prev, { id: `m${Date.now()}`, owner: memoryOwnerDraft, infer: inferDraft, raw: val, text }])
    setMemoryDraft('')
  }

  const resetDemo = () => {
    setQuery('')
    setKnowledge(SEED_KNOWLEDGE)
    setMemory(SEED_MEMORY)
  }

  return (
    <div className="kmp-root" id="try-it-live">
      <style>{`
        .kmp-root, .kmp-root *, .kmp-root *::before, .kmp-root *::after { box-sizing: border-box; }
        .kmp-root {
          --kmp-bg: #f5f4f8;
          --kmp-surface: #ffffff;
          --kmp-surface-2: #faf9fc;
          --kmp-ink: #14131c;
          --kmp-ink-soft: #59566f;
          --kmp-ink-faint: #8b87a0;
          --kmp-border: #ddd9e8;
          --kmp-border-strong: #c6c1d9;
          --kmp-knowledge: #1d5fa8;
          --kmp-knowledge-ink: #0f3f73;
          --kmp-knowledge-soft: #e8f0fb;
          --kmp-knowledge-border: #b9d3ef;
          --kmp-memory: #6a3fd6;
          --kmp-memory-ink: #4423a3;
          --kmp-memory-soft: #f1ecfd;
          --kmp-memory-border: #d3c3f7;
          --kmp-focus: #1d5fa8;
          --kmp-font-display: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", "Fira Code", Consolas, monospace;

          display: block;
          max-width: 880px;
          margin: 32px auto;
          padding: 22px;
          background: var(--kmp-bg);
          border: 1px solid var(--kmp-border);
          border-radius: 14px;
          color: var(--kmp-ink);
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        .dark .kmp-root, [data-theme="dark"] .kmp-root {
          --kmp-bg: #131019;
          --kmp-surface: #1c1827;
          --kmp-surface-2: #211d30;
          --kmp-ink: #f1eef8;
          --kmp-ink-soft: #b3aec8;
          --kmp-ink-faint: #7d7893;
          --kmp-border: #322c44;
          --kmp-border-strong: #423a5c;
          --kmp-knowledge: #7fb2e8;
          --kmp-knowledge-ink: #cfe4fa;
          --kmp-knowledge-soft: #17263b;
          --kmp-knowledge-border: #2b4260;
          --kmp-memory: #b79af2;
          --kmp-memory-ink: #e3d6fb;
          --kmp-memory-soft: #241b38;
          --kmp-memory-border: #3c2e58;
          --kmp-focus: #9dc4ee;
        }
        .kmp-root ::selection { background: var(--kmp-memory-soft); color: var(--kmp-memory-ink); }
        .kmp-root *:focus-visible { outline: 2px solid var(--kmp-focus); outline-offset: 2px; border-radius: 4px; }

        .kmp-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
        .kmp-eyebrow { font-family: var(--kmp-font-display); font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--kmp-ink-faint); }
        .kmp-reset { font-size: 12.5px; color: var(--kmp-ink-soft); background: none; border: 1px solid var(--kmp-border); border-radius: 7px; padding: 5px 11px; cursor: pointer; }
        .kmp-reset:hover { border-color: var(--kmp-border-strong); color: var(--kmp-ink); }
        .kmp-sim-badge { font-family: var(--kmp-font-display); font-size: 10.5px; color: var(--kmp-ink-faint); border: 1px dashed var(--kmp-border-strong); padding: 3px 8px; border-radius: 999px; display: inline-block; margin-bottom: 18px; }

        .kmp-console { background: var(--kmp-surface); border: 1px solid var(--kmp-border); border-radius: 10px; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
        .kmp-api-label { font-family: var(--kmp-font-display); font-size: 11.5px; color: var(--kmp-ink-faint); }
        .kmp-api-label b { color: var(--kmp-ink-soft); }
        .kmp-query-row { display: flex; gap: 8px; }
        .kmp-query-row input[type="text"] { flex: 1; font-size: 14.5px; padding: 10px 13px; border-radius: 8px; border: 1px solid var(--kmp-border-strong); background: var(--kmp-surface-2); color: var(--kmp-ink); }

        .kmp-controls-row { display: flex; flex-wrap: wrap; gap: 20px; align-items: center; }
        .kmp-control-group { display: flex; align-items: center; gap: 8px; }
        .kmp-control-label { font-family: var(--kmp-font-display); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--kmp-ink-faint); }
        .kmp-pillset { display: inline-flex; background: var(--kmp-surface-2); border: 1px solid var(--kmp-border); border-radius: 999px; padding: 3px; gap: 2px; }
        .kmp-pill { font-size: 12.5px; font-weight: 500; border: none; background: transparent; color: var(--kmp-ink-soft); padding: 5px 12px; border-radius: 999px; cursor: pointer; }
        .kmp-pill[data-active="true"] { background: var(--kmp-surface); color: var(--kmp-ink); box-shadow: 0 1px 2px rgba(20,19,28,0.08); }
        .kmp-pill.kmp-tk[data-active="true"] { color: var(--kmp-knowledge-ink); }
        .kmp-pill.kmp-tm[data-active="true"] { color: var(--kmp-memory-ink); }

        .kmp-results { margin-top: 16px; }
        .kmp-results-meta { font-family: var(--kmp-font-display); font-size: 11.5px; color: var(--kmp-ink-faint); margin: 0 0 8px; }
        .kmp-results-list { display: flex; flex-direction: column; gap: 8px; }
        .kmp-card { display: flex; gap: 10px; background: var(--kmp-surface); border: 1px solid var(--kmp-border); border-radius: 9px; padding: 11px 13px; }
        .kmp-bar { width: 3px; border-radius: 3px; flex-shrink: 0; }
        .kmp-bar.knowledge { background: var(--kmp-knowledge); }
        .kmp-bar.memory { background: var(--kmp-memory); }
        .kmp-card-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
        .kmp-card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .kmp-chip { font-family: var(--kmp-font-display); font-size: 10px; font-weight: 700; letter-spacing: 0.05em; padding: 2px 7px; border-radius: 5px; text-transform: uppercase; }
        .kmp-chip.knowledge { color: var(--kmp-knowledge-ink); background: var(--kmp-knowledge-soft); border: 1px solid var(--kmp-knowledge-border); }
        .kmp-chip.memory { color: var(--kmp-memory-ink); background: var(--kmp-memory-soft); border: 1px solid var(--kmp-memory-border); }
        .kmp-card-scope { font-size: 11.5px; color: var(--kmp-ink-faint); }
        .kmp-card-text { font-size: 14px; color: var(--kmp-ink); }
        .kmp-card-raw { font-size: 12px; color: var(--kmp-ink-faint); border-left: 2px solid var(--kmp-border); padding-left: 8px; }
        .kmp-empty { font-size: 13.5px; color: var(--kmp-ink-faint); padding: 20px 0; text-align: center; }

        .kmp-ingest-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 18px; }
        .kmp-panel { border-radius: 10px; padding: 15px; display: flex; flex-direction: column; gap: 10px; border: 1px solid; }
        .kmp-panel.knowledge { background: var(--kmp-knowledge-soft); border-color: var(--kmp-knowledge-border); }
        .kmp-panel.memory { background: var(--kmp-memory-soft); border-color: var(--kmp-memory-border); }
        .kmp-panel-title { font-family: var(--kmp-font-display); font-size: 13px; font-weight: 700; }
        .kmp-panel.knowledge .kmp-panel-title { color: var(--kmp-knowledge-ink); }
        .kmp-panel.memory .kmp-panel-title { color: var(--kmp-memory-ink); }
        .kmp-panel-sub { font-size: 12px; color: var(--kmp-ink-soft); margin-top: 2px; }
        .kmp-panel textarea { font-size: 13.5px; padding: 9px 11px; border-radius: 7px; border: 1px solid var(--kmp-border-strong); background: var(--kmp-surface); color: var(--kmp-ink); resize: vertical; min-height: 60px; width: 100%; font-family: inherit; }
        .kmp-panel-controls { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
        .kmp-owner-select { font-size: 12.5px; padding: 5px 8px; border-radius: 7px; border: 1px solid var(--kmp-border-strong); background: var(--kmp-surface); color: var(--kmp-ink); }
        .kmp-infer-toggle { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--kmp-ink-soft); cursor: pointer; background: none; border: none; padding: 0; }
        .kmp-switch { position: relative; width: 32px; height: 18px; border-radius: 999px; background: var(--kmp-border-strong); flex-shrink: 0; }
        .kmp-switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: var(--kmp-surface); transition: transform 0.15s ease; }
        .kmp-infer-toggle[data-active="true"] .kmp-switch { background: var(--kmp-memory); }
        .kmp-infer-toggle[data-active="true"] .kmp-switch::after { transform: translateX(14px); }
        .kmp-infer-hint { font-size: 11.5px; color: var(--kmp-ink-faint); min-height: 15px; }
        .kmp-add-btn { font-size: 13px; font-weight: 600; border: none; border-radius: 7px; padding: 8px 13px; cursor: pointer; align-self: flex-start; color: #fff; }
        .kmp-panel.knowledge .kmp-add-btn { background: var(--kmp-knowledge); }
        .kmp-panel.memory .kmp-add-btn { background: var(--kmp-memory); }

        .kmp-mapping { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; border-top: 1px solid var(--kmp-border); padding-top: 16px; margin-top: 18px; }
        .kmp-map-key { font-family: var(--kmp-font-display); font-size: 12px; color: var(--kmp-ink); font-weight: 600; display: block; }
        .kmp-map-desc { font-size: 11.5px; color: var(--kmp-ink-faint); }

        @media (max-width: 640px) {
          .kmp-ingest-grid, .kmp-mapping { grid-template-columns: 1fr; }
          .kmp-query-row { flex-direction: column; }
        }
      `}</style>

      <div className="kmp-head">
        <span className="kmp-eyebrow">Try it live</span>
        <button className="kmp-reset" type="button" onClick={resetDemo}>Reset demo</button>
      </div>
      <span className="kmp-sim-badge">Simulated in your browser · no live HydraDB connection</span>

      <div className="kmp-console">
        <span className="kmp-api-label"><b>POST</b> /query</span>
        <div className="kmp-query-row">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={'Search… try "dark mode" or "refund"'}
          />
        </div>
        <div className="kmp-controls-row">
          <div className="kmp-control-group">
            <span className="kmp-control-label">Viewing as</span>
            <div className="kmp-pillset">
              <button className="kmp-pill" data-active={persona === 'alice'} type="button" onClick={() => setPersona('alice')}>Alice</button>
              <button className="kmp-pill" data-active={persona === 'bob'} type="button" onClick={() => setPersona('bob')}>Bob</button>
            </div>
          </div>
          <div className="kmp-control-group">
            <span className="kmp-control-label">Store · type</span>
            <div className="kmp-pillset">
              <button className="kmp-pill" data-active={typeFilter === 'all'} type="button" onClick={() => setTypeFilter('all')}>All</button>
              <button className="kmp-pill kmp-tk" data-active={typeFilter === 'knowledge'} type="button" onClick={() => setTypeFilter('knowledge')}>Knowledge</button>
              <button className="kmp-pill kmp-tm" data-active={typeFilter === 'memory'} type="button" onClick={() => setTypeFilter('memory')}>Memory</button>
            </div>
          </div>
        </div>
      </div>

      <div className="kmp-results">
        <p className="kmp-results-meta">{metaText}</p>
        <div className="kmp-results-list">
          {results.length === 0 && (
            <p className="kmp-empty">
              {typeFilter === 'memory'
                ? `No memories match for ${personaName(persona)} — try switching persona or adding one below.`
                : 'No results. Try clearing the search or adding content below.'}
            </p>
          )}
          {results.map((r) => (
            <div className="kmp-card" key={r.id}>
              <div className={`kmp-bar ${r.kind}`} />
              <div className="kmp-card-body">
                <div className="kmp-card-top">
                  <span className={`kmp-chip ${r.kind}`}>{r.kind}</span>
                  <span className="kmp-card-scope">{r.scope}</span>
                </div>
                <div className="kmp-card-text">{r.text}</div>
                {r.raw && <div className="kmp-card-raw">inferred from: “{r.raw}”</div>}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="kmp-ingest-grid">
        <div className="kmp-panel knowledge">
          <div>
            <span className="kmp-panel-title">+ Add Knowledge</span>
            <div className="kmp-panel-sub">Shared with every user in this database.</div>
          </div>
          <textarea
            value={knowledgeDraft}
            onChange={(e) => setKnowledgeDraft(e.target.value)}
            placeholder="e.g. Our refund policy is 30 days from purchase, no questions asked."
          />
          <button className="kmp-add-btn" type="button" onClick={addKnowledge}>Add to Knowledge</button>
        </div>

        <div className="kmp-panel memory">
          <div>
            <span className="kmp-panel-title">+ Add Memory</span>
            <div className="kmp-panel-sub">Private to one person.</div>
          </div>
          <div className="kmp-panel-controls">
            <select className="kmp-owner-select" value={memoryOwnerDraft} onChange={(e) => setMemoryOwnerDraft(e.target.value)}>
              <option value="alice">For Alice</option>
              <option value="bob">For Bob</option>
            </select>
            <button className="kmp-infer-toggle" type="button" data-active={inferDraft} onClick={() => setInferDraft((v) => !v)}>
              <span className="kmp-switch" />
              <span>infer: {inferDraft ? 'true' : 'false'}</span>
            </button>
          </div>
          <span className="kmp-infer-hint">
            {inferDraft ? 'HydraDB would extract the preference or trait from this text.' : 'Stored exactly as written — no extraction.'}
          </span>
          <textarea
            value={memoryDraft}
            onChange={(e) => setMemoryDraft(e.target.value)}
            placeholder="e.g. I really like dark mode and hate long paragraphs."
          />
          <button className="kmp-add-btn" type="button" onClick={addMemory}>Add to Memory</button>
        </div>
      </div>

      <div className="kmp-mapping">
        <div>
          <span className="kmp-map-key">type: "knowledge" | "memory" | "all"</span>
          <span className="kmp-map-desc">Picks which store (or both) a query searches. Same endpoint either way.</span>
        </div>
        <div>
          <span className="kmp-map-key">collection</span>
          <span className="kmp-map-desc">Scopes memories to one user. Knowledge ignores it — it's visible to everyone.</span>
        </div>
        <div>
          <span className="kmp-map-key">infer: true | false</span>
          <span className="kmp-map-desc">true distills raw input into a preference/trait. false stores it exactly as written.</span>
        </div>
      </div>
    </div>
  )
}
