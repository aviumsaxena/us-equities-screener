import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { searchCompanies, type SearchHit } from "../api";
import { formatValue } from "../format";

/** Look a company up by ticker or name, as an alternative to building a screen.
 *  Ranking lives on the server (exact ticker > ticker prefix > name prefix >
 *  match anywhere, tie-broken on market cap), so "micro" surfaces Microsoft
 *  rather than a microcap. */
export function CompanySearch() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);

  const search = useMutation({
    mutationFn: (q: string) => searchCompanies(q, 8),
    onSuccess: setHits,
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) {
      setHits(null);
      return;
    }
    search.mutate(q);
  };

  const clear = () => {
    setQuery("");
    setHits(null);
    search.reset();
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Find a company</h2>
        {hits !== null && (
          <button className="chip" onClick={clear} type="button">
            Clear
          </button>
        )}
      </div>

      <form className="search" onSubmit={submit}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Company name or ticker — e.g. Apple, or AAPL"
          aria-label="Company name or ticker"
        />
        <button className="primary" type="submit" disabled={search.isPending}>
          {search.isPending ? "Searching…" : "Search"}
        </button>
      </form>

      {search.isError && <p className="error">{(search.error as Error).message}</p>}

      {hits !== null && hits.length === 0 && !search.isPending && (
        <p className="muted">
          No company matches “{query.trim()}”. Only companies with reported
          fundamentals are searchable.
        </p>
      )}

      {hits !== null && hits.length > 0 && (
        <ul className="hits">
          {hits.map((hit) => (
            <li key={hit.security_id}>
              <Link to={`/company/${hit.security_id}`}>
                <span className="hit-ticker">{hit.ticker ?? "—"}</span>
                <span className="hit-name">{hit.name ?? "—"}</span>
                <span className="hit-sector muted">{hit.sector ?? "—"}</span>
                <span className="hit-num">{formatValue(hit.price, "price")}</span>
                <span className="hit-num">{formatValue(hit.market_cap, "currency")}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
