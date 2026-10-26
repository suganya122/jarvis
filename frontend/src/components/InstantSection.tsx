import { useState } from "react";

export interface RouterSettings {
  enabled: boolean;
  direct_without_asking: boolean;
  search_url: string;
  [key: string]: unknown;
}

export function InstantSection({ router, onSave }: { router: RouterSettings; onSave: (r: RouterSettings) => void }) {
  const [enabled, setEnabled] = useState(router.enabled);
  const [direct, setDirect] = useState(router.direct_without_asking);
  const [searchUrl, setSearchUrl] = useState(router.search_url);
  const validUrl = /^https?:\/\/.+\{query\}/.test(searchUrl);

  return (
    <section aria-labelledby="instant-title">
      <h3 id="instant-title">Instant commands</h3>
      <p className="hint">
        Simple requests like “open Firefox”, “search for …”, “next song”, “what time is it”, or “15% of 80” run
        immediately with no AI call, so they cost nothing and work offline.
      </p>
      <div className="stack">
        <label className="radio">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Handle simple requests instantly
        </label>
        <label className="radio">
          <input type="checkbox" checked={direct} onChange={(e) => setDirect(e.target.checked)} />
          Don't ask before running a command I typed myself (destructive actions still ask)
        </label>
        <label>
          Search address (use {"{query}"} for the search terms; a SearXNG URL works too)
          <input
            value={searchUrl}
            onChange={(e) => setSearchUrl(e.target.value)}
            aria-invalid={!validUrl || undefined}
          />
        </label>
        {!validUrl && <p className="error-line">Must start with http(s):// and contain {"{query}"}.</p>}
      </div>
      <div className="row">
        <button
          type="button"
          disabled={!validUrl}
          onClick={() => onSave({ ...router, enabled, direct_without_asking: direct, search_url: searchUrl })}
        >
          Save instant commands
        </button>
      </div>
    </section>
  );
}
