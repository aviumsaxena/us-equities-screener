// Typed client for the FastAPI read layer. Paths go through Vite's /api proxy
// in dev (see vite.config.ts), so the browser stays same-origin.

const BASE = "/api";

export type FieldKind = "numeric" | "text" | "boolean";

/** One screenable field, as advertised by the server's whitelist. */
export interface FieldInfo {
  field: string;
  kind: FieldKind;
  ops: string[];
}

export interface Condition {
  field: string;
  op: string;
  value: unknown;
}

export interface Group {
  op: "AND" | "OR";
  rules: (Group | Condition)[];
}

export interface ScreenRow {
  security_id: number;
  ticker: string | null;
  name: string | null;
  sector: string | null;
  industry: string | null;
  exchange: string | null;
  price: number | null;
  market_cap: number | null;
  pe_ttm: number | null;
  pb: number | null;
  ps_ttm: number | null;
  roe: number | null;
  net_margin: number | null;
  revenue_growth_yoy: number | null;
  revenue_cagr_3y: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  rev_up_4q: boolean | null;
  profitable_5y: boolean | null;
  fundamentals_asof: string | null;
}

export interface ScreenResponse {
  results: ScreenRow[];
  count: number;
  next_cursor: string | null;
  cached: boolean;
}

export interface ScreenRequest {
  filter: Group;
  limit?: number;
  cursor?: string | null;
}

export interface PeriodRow {
  period_end: string;
  fiscal_year: number | null;
  fiscal_period: string | null;
  revenue: number | null;
  net_income: number | null;
  eps_diluted: number | null;
  total_assets: number | null;
  total_equity: number | null;
  total_debt: number | null;
  operating_cf: number | null;
  free_cf: number | null;
}

export interface CompanyResponse {
  company: Record<string, string | number | boolean | null>;
  history: PeriodRow[];
  cached: boolean;
}

export interface PriceBar {
  dt: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    // the compiler rejects bad screens with 400 + {"detail": "..."}; surface
    // that message rather than a generic failure
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body; keep the status message */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const getFields = () => request<FieldInfo[]>("/fields");

export const runScreen = (body: ScreenRequest) =>
  request<ScreenResponse>("/screen", { method: "POST", body: JSON.stringify(body) });

export const getCompany = (id: number) => request<CompanyResponse>(`/company/${id}`);

export const getPrices = (id: number, days = 120) =>
  request<PriceBar[]>(`/prices/${id}?days=${days}`);
