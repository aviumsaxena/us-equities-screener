// Presentation layer for screener fields.
//
// The server owns *validity* (which fields/operators exist — see GET /fields);
// the client owns *presentation* (labels, units, formatting). A field the
// server exposes without an entry here still renders, using a prettified name
// and a format inferred from its kind — so adding a metric to the whitelist
// never breaks the UI.

import type { FieldKind } from "./api";

export type Format = "currency" | "percent" | "ratio" | "price" | "text" | "bool";

interface FieldMeta {
  label: string;
  format: Format;
  /** shown under the value input, e.g. to explain that 0.15 means 15% */
  hint?: string;
}

const FIELD_META: Record<string, FieldMeta> = {
  ticker: { label: "Ticker", format: "text" },
  sector: { label: "Sector", format: "text" },
  industry: { label: "Industry", format: "text" },
  exchange: { label: "Exchange", format: "text" },

  price: { label: "Price", format: "price" },
  market_cap: { label: "Market cap", format: "currency", hint: "e.g. 1e9 = $1B" },

  pe_ttm: { label: "P/E (TTM)", format: "ratio" },
  pb: { label: "P/B", format: "ratio" },
  ps_ttm: { label: "P/S (TTM)", format: "ratio" },
  ev_ebitda: { label: "EV/EBITDA", format: "ratio" },
  dividend_yield: { label: "Dividend yield", format: "percent", hint: "0.03 = 3%" },

  gross_margin: { label: "Gross margin", format: "percent", hint: "0.4 = 40%" },
  operating_margin: { label: "Operating margin", format: "percent", hint: "0.2 = 20%" },
  net_margin: { label: "Net margin", format: "percent", hint: "0.15 = 15%" },
  roe: { label: "ROE", format: "percent", hint: "0.15 = 15%" },
  roce: { label: "ROCE", format: "percent", hint: "0.15 = 15%" },

  revenue_ttm: { label: "Revenue (TTM)", format: "currency" },
  revenue_growth_yoy: { label: "Revenue growth YoY", format: "percent", hint: "0.1 = 10%" },
  eps_growth_yoy: { label: "EPS growth YoY", format: "percent", hint: "0.1 = 10%" },
  revenue_cagr_3y: { label: "Revenue CAGR 3y", format: "percent", hint: "0.1 = 10%" },

  debt_to_equity: { label: "Debt / equity", format: "ratio" },
  current_ratio: { label: "Current ratio", format: "ratio" },
  interest_coverage: { label: "Interest coverage", format: "ratio" },

  rev_up_4q: { label: "Revenue up 4 quarters", format: "bool" },
  profitable_5y: { label: "Profitable 5 years", format: "bool" },
};

const FALLBACK_FORMAT: Record<FieldKind, Format> = {
  numeric: "ratio",
  text: "text",
  boolean: "bool",
};

function prettify(field: string): string {
  return field.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function labelFor(field: string): string {
  return FIELD_META[field]?.label ?? prettify(field);
}

export function hintFor(field: string): string | undefined {
  return FIELD_META[field]?.hint;
}

export function formatFor(field: string, kind: FieldKind): Format {
  return FIELD_META[field]?.format ?? FALLBACK_FORMAT[kind];
}

export function money(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(1)}M`;
  // Nano-caps are real across a 7.6k universe — Pineapple Financial's market cap
  // is ~$642k. Without a K tier it rendered as "$642249.97" next to "$4.76T",
  // which reads as a broken number rather than a small company.
  if (abs >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${value.toFixed(2)}`;
}

/** Renders a value for display. Null/undefined become an em dash — a missing
 *  metric is a real signal here (see ARCHITECTURE §6), not an error. */
export function formatValue(value: unknown, format: Format): string {
  if (value === null || value === undefined || value === "") return "—";

  switch (format) {
    case "currency":
      return typeof value === "number" ? money(value) : String(value);
    case "price":
      return typeof value === "number" ? `$${value.toFixed(2)}` : String(value);
    case "percent":
      return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : String(value);
    case "ratio":
      return typeof value === "number" ? value.toFixed(2) : String(value);
    case "bool":
      return value ? "Yes" : "No";
    default:
      return String(value);
  }
}

export function formatField(field: string, value: unknown, kind: FieldKind = "numeric"): string {
  return formatValue(value, formatFor(field, kind));
}
