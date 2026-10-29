export const ContextDecider = () => {
  const [audience, setAudience] = useState(null);
  const [signal, setSignal] = useState(null);

  const reset = () => {
    setAudience(null);
    setSignal(null);
  };

  const result =
    audience === 'shared'
      ? {
          title: 'Use Knowledge',
          summary:
            'Shared, database-wide context. Ingest with type=knowledge.',
          request: `curl -X POST 'https://api.hydradb.com/context/ingest' \\
  -H "Authorization: Bearer $HYDRA_DB_API_KEY" \\
  -H "API-Version: 2" \\
  -F "type=knowledge" \\
  -F "database=acme_corp" \\
  -F "collection=team_docs" \\
  -F "documents=@/path/to/policy.pdf"`,
          next: 'Poll GET /context/status, then POST /query with type: "knowledge" (or "all" with collections that include team_docs).',
          href: '/essentials/v2/knowledge',
        }
      : audience === 'personal' && signal === 'raw'
        ? {
            title: 'Use Memory with infer: true',
            summary:
              'User-scoped raw signal. Let HydraDB extract the useful preference or fact.',
            request: `curl -X POST 'https://api.hydradb.com/context/ingest' \\
  -H "Authorization: Bearer $HYDRA_DB_API_KEY" \\
  -H "API-Version: 2" \\
  -F "type=memory" \\
  -F "database=acme_corp" \\
  -F "collection=user_alex" \\
  -F 'memories=[{"text":"User toggled dark mode and kept it for a month.","infer":true}]'`,
            next: 'Poll GET /context/status, then POST /query with type: "memory" or "all".',
            href: '/essentials/v2/memories',
          }
        : audience === 'personal' && signal === 'structured'
          ? {
              title: 'Use Memory with infer: false',
              summary:
                'User-scoped fact you already wrote. Store exactly what you send.',
              request: `curl -X POST 'https://api.hydradb.com/context/ingest' \\
  -H "Authorization: Bearer $HYDRA_DB_API_KEY" \\
  -H "API-Version: 2" \\
  -F "type=memory" \\
  -F "database=acme_corp" \\
  -F "collection=user_alex" \\
  -F 'memories=[{"text":"User plan tier is Pro.","infer":false}]'`,
              next: 'Poll GET /context/status, then POST /query with type: "memory" or "all".',
              href: '/essentials/v2/memories',
            }
          : null;

  const optionClass = (selected) =>
    `rounded-xl border px-4 py-3 text-left transition ${
      selected
        ? 'border-primary bg-primary/10 dark:bg-primary/20'
        : 'border-gray-200 dark:border-gray-700 hover:border-primary/60'
    }`;

  return (
    <div className="not-prose my-6 rounded-2xl border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/40 p-5 space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            Context choice picker
          </p>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            Answer the prompts below. Get the recommended path and a verified request.
          </p>
        </div>
        {(audience || signal) && (
          <button
            type="button"
            onClick={reset}
            className="text-xs font-medium text-primary hover:underline shrink-0"
          >
            Reset
          </button>
        )}
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
          1. Who is this context for?
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            aria-pressed={audience === 'shared'}
            className={optionClass(audience === 'shared')}
            onClick={() => {
              setAudience('shared');
              setSignal(null);
            }}
          >
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Everyone in the database
            </div>
            <div className="text-xs text-gray-600 dark:text-gray-400 mt-1">
              Docs, policies, wikis, shared runbooks
            </div>
          </button>
          <button
            type="button"
            aria-pressed={audience === 'personal'}
            className={optionClass(audience === 'personal')}
            onClick={() => setAudience('personal')}
          >
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              One user, workspace, or session
            </div>
            <div className="text-xs text-gray-600 dark:text-gray-400 mt-1">
              Preferences, traits, conversation history
            </div>
          </button>
        </div>
      </div>

      {audience === 'personal' && (
        <div className="space-y-2">
          <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
            2. Is the input already the memory?
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <button
              type="button"
              aria-pressed={signal === 'raw'}
              className={optionClass(signal === 'raw')}
              onClick={() => setSignal('raw')}
            >
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                No - raw signal to extract
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                Dialogue, logs, behavior, noisy notes
              </div>
            </button>
            <button
              type="button"
              aria-pressed={signal === 'structured'}
              className={optionClass(signal === 'structured')}
              onClick={() => setSignal('structured')}
            >
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                Yes - already structured
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                Facts and preferences you already wrote
              </div>
            </button>
          </div>
        </div>
      )}

      {result && (
        <div className="rounded-xl border border-primary/30 bg-white dark:bg-black/40 p-4 space-y-3">
          <div>
            <p className="text-sm font-semibold text-primary">{result.title}</p>
            <p className="text-sm text-gray-700 dark:text-gray-300 mt-1">
              {result.summary}
            </p>
          </div>
          <pre className="overflow-x-auto rounded-lg bg-gray-950 text-gray-100 text-xs leading-relaxed p-3">
            <code>{result.request}</code>
          </pre>
          <p className="text-xs text-gray-600 dark:text-gray-400">{result.next}</p>
          <a
            href={result.href}
            className="inline-flex text-xs font-medium text-primary hover:underline"
          >
            Open the full guide
          </a>
        </div>
      )}
    </div>
  );
};
