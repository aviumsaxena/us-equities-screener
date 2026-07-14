// The query builder renders itself from GET /fields, so every field and
// operator it offers is one the server's whitelist already accepts. Adding a
// metric to the whitelist makes it screenable here with no frontend change.
//
// Scope: a flat rule list joined by one AND/OR. The compiler supports nested
// groups; exposing that needs a tree UI and is deliberately left for later.

import type { FieldInfo, Group } from "../api";
import { hintFor, labelFor } from "../format";

export interface Rule {
  id: number;
  field: string;
  op: string;
  /** raw input state; coerced to the API's value shape on submit */
  value: string;
  /** second input, only used by BETWEEN */
  value2: string;
}

export interface Preset {
  name: string;
  groupOp: "AND" | "OR";
  rules: Omit<Rule, "id">[];
}

/** One-click screens. Data changes at most daily, so these are the queries
 *  most likely to be cache hits. */
export const PRESETS: Preset[] = [
  {
    name: "Growth at a reasonable price",
    groupOp: "AND",
    rules: [
      { field: "pe_ttm", op: "<", value: "35", value2: "" },
      { field: "revenue_growth_yoy", op: ">", value: "0.10", value2: "" },
    ],
  },
  {
    name: "High-ROE compounders",
    groupOp: "AND",
    rules: [
      { field: "roe", op: ">", value: "0.25", value2: "" },
      { field: "profitable_5y", op: "=", value: "true", value2: "" },
    ],
  },
  {
    name: "Defensive quality",
    groupOp: "AND",
    rules: [
      { field: "sector", op: "IN", value: "Consumer Staples, Health Care", value2: "" },
      { field: "current_ratio", op: ">", value: "1.0", value2: "" },
    ],
  },
];

/** Coerce a rule's raw text input into the JSON shape the compiler expects. */
function toValue(rule: Rule, kind: FieldInfo["kind"]): unknown {
  const asScalar = (raw: string): unknown => {
    const text = raw.trim();
    if (kind === "numeric") return Number(text);
    if (kind === "boolean") return text === "true";
    return text;
  };

  if (rule.op === "BETWEEN") return [asScalar(rule.value), asScalar(rule.value2)];
  if (rule.op === "IN") {
    return rule.value
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
      .map(asScalar);
  }
  return asScalar(rule.value);
}

/** Parts of a rule that must each parse as a value (BETWEEN has two, IN has n). */
function valueParts(rule: Rule): string[] {
  if (rule.op === "BETWEEN") return [rule.value, rule.value2];
  if (rule.op === "IN") {
    return rule.value
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
  }
  return [rule.value];
}

/** Catch bad input before it reaches the server.
 *
 *  The compiler is the security boundary and rejects this anyway, but an
 *  unparseable number would go out as NaN — which JSON.stringify silently
 *  turns into `null` — so the user would get "expected a number, got None"
 *  about a value they never typed. Fail here with the field's own name. */
export function validateRules(rules: Rule[], fields: FieldInfo[]): string | null {
  if (rules.length === 0) return "Add at least one filter before running a screen.";

  for (const rule of rules) {
    const kind = fields.find((f) => f.field === rule.field)?.kind ?? "numeric";
    if (kind === "boolean") continue;

    const label = labelFor(rule.field);
    const parts = valueParts(rule);

    if (parts.length === 0 || parts.some((part) => part.trim() === "")) {
      return rule.op === "BETWEEN"
        ? `${label}: enter both a minimum and a maximum.`
        : `${label}: enter a value.`;
    }
    if (kind === "numeric") {
      const bad = parts.find((part) => !Number.isFinite(Number(part)));
      if (bad !== undefined) return `${label}: “${bad}” isn’t a number.`;
    }
  }
  return null;
}

/** Rules -> the predicate tree POSTed to /screen. */
export function buildFilter(rules: Rule[], groupOp: "AND" | "OR", fields: FieldInfo[]): Group {
  const kindOf = (name: string) =>
    fields.find((f) => f.field === name)?.kind ?? "numeric";
  return {
    op: groupOp,
    rules: rules.map((rule) => ({
      field: rule.field,
      op: rule.op,
      value: toValue(rule, kindOf(rule.field)),
    })),
  };
}

interface Props {
  fields: FieldInfo[];
  rules: Rule[];
  groupOp: "AND" | "OR";
  onChange: (rules: Rule[]) => void;
  onGroupOpChange: (op: "AND" | "OR") => void;
  onRun: () => void;
  running: boolean;
}

export function QueryBuilder({
  fields,
  rules,
  groupOp,
  onChange,
  onGroupOpChange,
  onRun,
  running,
}: Props) {
  const specFor = (name: string) => fields.find((f) => f.field === name);

  const update = (id: number, patch: Partial<Rule>) =>
    onChange(rules.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const changeField = (id: number, field: string) => {
    const spec = specFor(field);
    const rule = rules.find((r) => r.id === id);
    // keep the operator if the new field still allows it, else fall back
    const op = spec && rule && spec.ops.includes(rule.op) ? rule.op : spec?.ops[0] ?? "=";
    update(id, { field, op, value: "", value2: "" });
  };

  const addRule = () => {
    const first = fields[0];
    onChange([
      ...rules,
      {
        id: Date.now(),
        field: first?.field ?? "pe_ttm",
        op: first?.ops.includes("<") ? "<" : first?.ops[0] ?? "=",
        value: "",
        value2: "",
      },
    ]);
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Filters</h2>
        <div className="joiner">
          {(["AND", "OR"] as const).map((op) => (
            <button
              key={op}
              className={groupOp === op ? "chip chip-on" : "chip"}
              onClick={() => onGroupOpChange(op)}
              type="button"
            >
              {op}
            </button>
          ))}
        </div>
      </div>

      {rules.length === 0 && <p className="muted">No filters — add one, or pick a preset below.</p>}

      <div className="rules">
        {rules.map((rule) => {
          const spec = specFor(rule.field);
          const kind = spec?.kind ?? "numeric";
          const hint = hintFor(rule.field);
          return (
            <div className="rule" key={rule.id}>
              <select
                value={rule.field}
                onChange={(e) => changeField(rule.id, e.target.value)}
                aria-label="Field"
              >
                {fields.map((f) => (
                  <option key={f.field} value={f.field}>
                    {labelFor(f.field)}
                  </option>
                ))}
              </select>

              <select
                className="op"
                value={rule.op}
                onChange={(e) => update(rule.id, { op: e.target.value, value: "", value2: "" })}
                aria-label="Operator"
              >
                {(spec?.ops ?? []).map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </select>

              {kind === "boolean" ? (
                <select
                  value={rule.value || "true"}
                  onChange={(e) => update(rule.id, { value: e.target.value })}
                  aria-label="Value"
                >
                  <option value="true">Yes</option>
                  <option value="false">No</option>
                </select>
              ) : rule.op === "BETWEEN" ? (
                <span className="between">
                  <input
                    value={rule.value}
                    onChange={(e) => update(rule.id, { value: e.target.value })}
                    placeholder="min"
                    aria-label="Minimum"
                  />
                  <input
                    value={rule.value2}
                    onChange={(e) => update(rule.id, { value2: e.target.value })}
                    placeholder="max"
                    aria-label="Maximum"
                  />
                </span>
              ) : (
                <input
                  value={rule.value}
                  onChange={(e) => update(rule.id, { value: e.target.value })}
                  placeholder={rule.op === "IN" ? "comma, separated, values" : hint ?? "value"}
                  aria-label="Value"
                />
              )}

              <button
                className="icon"
                onClick={() => onChange(rules.filter((r) => r.id !== rule.id))}
                title="Remove filter"
                type="button"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      <div className="actions">
        <button className="secondary" onClick={addRule} type="button">
          + Add filter
        </button>
        <button className="primary" onClick={onRun} disabled={running} type="button">
          {running ? "Running…" : "Run screen"}
        </button>
      </div>
    </section>
  );
}
