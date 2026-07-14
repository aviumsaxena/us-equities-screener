import { Link } from "react-router-dom";

import type { ScreenRow } from "../api";
import { formatValue } from "../format";

/** Columns shown for every screen. The API returns a fixed projection, so this
 *  stays a plain table — at 100 rows/page there's nothing to virtualize yet.
 *  Virtualization only earns its place once page sizes grow. */
const COLUMNS: { key: keyof ScreenRow; label: string; format: Parameters<typeof formatValue>[1]; numeric?: boolean }[] = [
  { key: "sector", label: "Sector", format: "text" },
  { key: "price", label: "Price", format: "price", numeric: true },
  { key: "market_cap", label: "Mkt cap", format: "currency", numeric: true },
  { key: "pe_ttm", label: "P/E", format: "ratio", numeric: true },
  { key: "pb", label: "P/B", format: "ratio", numeric: true },
  { key: "roe", label: "ROE", format: "percent", numeric: true },
  { key: "net_margin", label: "Net margin", format: "percent", numeric: true },
  { key: "revenue_growth_yoy", label: "Rev growth", format: "percent", numeric: true },
  { key: "debt_to_equity", label: "D/E", format: "ratio", numeric: true },
];

interface Props {
  rows: ScreenRow[];
  count: number;
  cached: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}

export function ResultsGrid({ rows, count, cached, hasMore, loadingMore, onLoadMore }: Props) {
  if (rows.length === 0) {
    return (
      <section className="panel">
        <h2>Results</h2>
        <p className="muted">
          No companies match these filters. That can be a real finding — try loosening a bound.
        </p>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>
          Results <span className="muted">({count})</span>
        </h2>
        {cached && (
          <span className="badge" title="Served from the Redis screen cache">
            cached
          </span>
        )}
      </div>

      <div className="grid-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Name</th>
              {COLUMNS.map((c) => (
                <th key={c.key} className={c.numeric ? "num" : undefined}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.security_id}>
                <td>
                  <Link className="ticker" to={`/company/${row.security_id}`}>
                    {row.ticker ?? "—"}
                  </Link>
                </td>
                <td className="name" title={row.name ?? undefined}>
                  {row.name ?? "—"}
                </td>
                {COLUMNS.map((c) => (
                  <td key={c.key} className={c.numeric ? "num" : undefined}>
                    {formatValue(row[c.key], c.format)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hasMore && (
        <div className="actions">
          {/* keyset pagination — the server hands back an opaque cursor */}
          <button className="secondary" onClick={onLoadMore} disabled={loadingMore} type="button">
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </section>
  );
}
