import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getCompany, getPrices } from "../api";
import { PriceChart } from "../components/PriceChart";
import { formatValue, labelFor, money } from "../format";

/** Metric cards, grouped the way an analyst reads them. */
const CARDS: { title: string; fields: { key: string; format: Parameters<typeof formatValue>[1] }[] }[] = [
  {
    title: "Valuation",
    fields: [
      { key: "market_cap", format: "currency" },
      { key: "pe_ttm", format: "ratio" },
      { key: "pb", format: "ratio" },
      { key: "ps_ttm", format: "ratio" },
    ],
  },
  {
    title: "Profitability",
    fields: [
      { key: "roe", format: "percent" },
      { key: "roce", format: "percent" },
      { key: "net_margin", format: "percent" },
      { key: "operating_margin", format: "percent" },
    ],
  },
  {
    title: "Growth",
    fields: [
      { key: "revenue_ttm", format: "currency" },
      { key: "revenue_growth_yoy", format: "percent" },
      { key: "revenue_cagr_3y", format: "percent" },
      { key: "eps_growth_yoy", format: "percent" },
    ],
  },
  {
    title: "Balance sheet",
    fields: [
      { key: "debt_to_equity", format: "ratio" },
      { key: "current_ratio", format: "ratio" },
      { key: "interest_coverage", format: "ratio" },
      { key: "profitable_5y", format: "bool" },
    ],
  },
];

export function CompanyPage() {
  const { id } = useParams<{ id: string }>();
  const securityId = Number(id);

  const company = useQuery({
    queryKey: ["company", securityId],
    queryFn: () => getCompany(securityId),
    enabled: Number.isFinite(securityId),
  });

  const prices = useQuery({
    queryKey: ["prices", securityId],
    queryFn: () => getPrices(securityId, 120),
    enabled: Number.isFinite(securityId),
  });

  if (company.isLoading) return <p className="muted">Loading…</p>;
  if (company.isError) return <p className="error">{(company.error as Error).message}</p>;
  if (!company.data) return null;

  const c = company.data.company;
  const history = company.data.history;

  return (
    <>
      <Link className="back" to="/">
        ← Back to screener
      </Link>

      <header className="company-head">
        <div>
          <h1>
            {String(c.ticker ?? "—")} <span className="muted">{String(c.name ?? "")}</span>
          </h1>
          <p className="muted">
            {[c.sector, c.industry, c.exchange].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
        <div className="price-tag">
          <span className="price">{formatValue(c.price, "price")}</span>
          <span className="muted">as of {String(c.price_asof ?? "—")}</span>
        </div>
      </header>

      <section className="cards">
        {CARDS.map((card) => (
          <div className="card" key={card.title}>
            <h3>{card.title}</h3>
            <dl>
              {card.fields.map((f) => (
                <div key={f.key}>
                  <dt>{labelFor(f.key)}</dt>
                  <dd>{formatValue(c[f.key], f.format)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </section>

      <section className="panel">
        <h2>Price</h2>
        {prices.isLoading ? (
          <p className="muted">Loading chart…</p>
        ) : (
          <PriceChart bars={prices.data ?? []} />
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Financial history</h2>
          <span className="muted">fundamentals as of {String(c.fundamentals_asof ?? "—")}</span>
        </div>
        <div className="grid-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Period</th>
                <th className="num">Revenue</th>
                <th className="num">Net income</th>
                <th className="num">EPS (diluted)</th>
                <th className="num">Total assets</th>
                <th className="num">Total equity</th>
                <th className="num">Operating CF</th>
                <th className="num">Free CF</th>
              </tr>
            </thead>
            <tbody>
              {history.map((p) => (
                <tr key={p.period_end}>
                  <td>
                    {p.fiscal_year} {p.fiscal_period}
                    <span className="muted"> · {p.period_end}</span>
                  </td>
                  <td className="num">{p.revenue === null ? "—" : money(p.revenue)}</td>
                  <td className="num">{p.net_income === null ? "—" : money(p.net_income)}</td>
                  <td className="num">{formatValue(p.eps_diluted, "ratio")}</td>
                  <td className="num">{p.total_assets === null ? "—" : money(p.total_assets)}</td>
                  <td className="num">{p.total_equity === null ? "—" : money(p.total_equity)}</td>
                  <td className="num">{p.operating_cf === null ? "—" : money(p.operating_cf)}</td>
                  <td className="num">{p.free_cf === null ? "—" : money(p.free_cf)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
