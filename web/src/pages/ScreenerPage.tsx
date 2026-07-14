import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getFields, runScreen, type ScreenRow } from "../api";
import { PRESETS, QueryBuilder, buildFilter, validateRules, type Rule } from "../components/QueryBuilder";
import { ResultsGrid } from "../components/ResultsGrid";

const PAGE_SIZE = 25;

let nextId = 1;
const withIds = (rules: Omit<Rule, "id">[]): Rule[] =>
  rules.map((r) => ({ ...r, id: nextId++ }));

export function ScreenerPage() {
  const { data: fields = [], isLoading: fieldsLoading, error: fieldsError } = useQuery({
    queryKey: ["fields"],
    queryFn: getFields,
    staleTime: Infinity, // the whitelist only changes when the API is redeployed
  });

  const [rules, setRules] = useState<Rule[]>(() => withIds(PRESETS[0].rules));
  const [groupOp, setGroupOp] = useState<"AND" | "OR">("AND");

  const [rows, setRows] = useState<ScreenRow[]>([]);
  const [count, setCount] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cached, setCached] = useState(false);
  const [ran, setRan] = useState(false);
  const [invalid, setInvalid] = useState<string | null>(null);

  const screen = useMutation({
    mutationFn: (opts: { cursor: string | null }) =>
      runScreen({
        filter: buildFilter(rules, groupOp, fields),
        limit: PAGE_SIZE,
        cursor: opts.cursor,
      }),
    onSuccess: (data, vars) => {
      // a cursored call is "load more" — append; otherwise it's a fresh run
      setRows((prev) => (vars.cursor ? [...prev, ...data.results] : data.results));
      setCount((prev) => (vars.cursor ? prev + data.count : data.count));
      setCursor(data.next_cursor);
      setCached(data.cached);
      setRan(true);
    },
  });

  const run = () => {
    const problem = validateRules(rules, fields);
    setInvalid(problem);
    if (problem) return; // don't round-trip input the user can fix here
    screen.mutate({ cursor: null });
  };

  const applyPreset = (index: number) => {
    setRules(withIds(PRESETS[index].rules));
    setGroupOp(PRESETS[index].groupOp);
    setRows([]);
    setRan(false);
    setInvalid(null);
  };

  if (fieldsLoading) return <p className="muted">Loading fields…</p>;
  if (fieldsError) {
    return (
      <p className="error">
        Couldn’t reach the API: {(fieldsError as Error).message}. Is <code>uvicorn</code> running on
        :8000?
      </p>
    );
  }

  const isLoadMore = screen.isPending && screen.variables?.cursor != null;

  return (
    <>
      <section className="presets">
        {PRESETS.map((preset, i) => (
          <button key={preset.name} className="chip" onClick={() => applyPreset(i)} type="button">
            {preset.name}
          </button>
        ))}
      </section>

      <QueryBuilder
        fields={fields}
        rules={rules}
        groupOp={groupOp}
        onChange={setRules}
        onGroupOpChange={setGroupOp}
        onRun={run}
        running={screen.isPending && !isLoadMore}
      />

      {invalid && <p className="error">{invalid}</p>}
      {screen.isError && <p className="error">{(screen.error as Error).message}</p>}

      {ran && !screen.isError && (
        <ResultsGrid
          rows={rows}
          count={count}
          cached={cached}
          hasMore={cursor !== null}
          loadingMore={isLoadMore}
          onLoadMore={() => screen.mutate({ cursor })}
        />
      )}
    </>
  );
}
