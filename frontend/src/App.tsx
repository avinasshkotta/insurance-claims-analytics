import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { AskResult, Claim, Health, QueueEntry, QueueStats, UploadResult, Usage } from "./api";

type Activity = { id: number; time: string; text: string; tone: "ok" | "warn" | "info" };

const money = (n?: number | null) =>
  n == null || Number.isNaN(n)
    ? "—"
    : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

const clock = () =>
  new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [queueStats, setQueueStats] = useState<QueueStats | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [threshold, setThreshold] = useState(0.5);
  const [selected, setSelected] = useState<string>("CLM-1041");
  const [question, setQuestion] = useState("What is the daily rental reimbursement limit?");
  const [askResult, setAskResult] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [batching, setBatching] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [lastUpload, setLastUpload] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);

  const log = (text: string, tone: Activity["tone"] = "info") =>
    setActivity((a) => [{ id: Date.now() + Math.random(), time: clock(), text, tone }, ...a].slice(0, 12));

  async function loadAll() {
    try {
      const [h, c, q, u] = await Promise.all([
        api.health(),
        api.claims(),
        api.queue(),
        api.usage(),
      ]);
      setHealth(h);
      setClaims(c.claims);
      setThreshold(c.threshold);
      setQueue(q.entries);
      setQueueStats(q.stats);
      setUsage(u);
      setError(null);
    } catch (e) {
      setError(`Cannot reach the API: ${(e as Error).message}. Is uvicorn running on :8000?`);
    }
  }

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, 30000);
    return () => clearInterval(t);
  }, []);

  const selectedClaim = useMemo(
    () => claims.find((c) => c.claim_id === selected) ?? null,
    [claims, selected]
  );

  const stats = useMemo(() => {
    const flagged = claims.filter((c) => c.risk.flagged).length;
    return {
      total: claims.length,
      flagged,
      routed: queue.length,
      passRate: claims.length ? Math.round(((claims.length - flagged) / claims.length) * 100) : 0,
    };
  }, [claims, queue]);

  async function triage(id: string) {
    setBusy((b) => ({ ...b, [id]: true }));
    try {
      const r = await api.triage(id);
      const decision = r.queue_entry ? "ROUTED" : "passed";
      log(
        `${r.claim_id} ${decision} · risk ${r.risk.score.toFixed(2)} · tools: ${r.tool_log
          .map((t) => t.tool)
          .join(" → ")}`,
        r.queue_entry ? "warn" : "ok"
      );
      await loadAll();
    } catch (e) {
      log(`${id} triage failed: ${(e as Error).message}`, "warn");
      setError((e as Error).message);
    } finally {
      setBusy((b) => ({ ...b, [id]: false }));
    }
  }

  async function triageAll() {
    setBatching(true);
    const targets = claims.filter((c) => c.status === "open" || c.status === "under_review");
    log(`Running full triage over ${targets.length} claims…`);
    for (const c of targets) {
      await triage(c.claim_id);
    }
    setBatching(false);
    log("Batch complete. Queue refreshed.", "ok");
    await loadAll();
  }

  async function ask() {
    if (!selectedClaim) return;
    setAsking(true);
    setAskResult(null);
    try {
      const r = await api.ask(selectedClaim.claim_id, question);
      setAskResult(r);
      log(`${selectedClaim.claim_id} policy answer · ${r.citations.length} citation(s)`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAsking(false);
    }
  }

  async function resetQueue() {
    await api.resetQueue();
    log("Case queue cleared.", "info");
    await loadAll();
  }

  async function decide(claim_id: string, decision: "accepted" | "overridden") {
    let reason: string | null = null;
    if (decision === "overridden") {
      reason = window.prompt("Why is this routing being overridden?") ?? "";
    }
    try {
      await api.decide(claim_id, decision, reason ?? undefined);
      log(`${claim_id} ${decision}${reason ? ` · ${reason}` : ""}`,
          decision === "accepted" ? "ok" : "warn");
      await loadAll();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleUpload(file: File) {
    if (uploading) return;
    setUploading(true);
    setError(null);
    try {
      const result = await api.upload(file);
      setLastUpload(result);
      setSelected(result.claim.claim_id);
      log(
        `Ingested ${file.name} → ${result.claim.claim_id} · ${result.extraction.format.toUpperCase()}`
          + ` · ${result.extraction.attempts} attempt(s) · ${result.extraction.status}`,
        result.extraction.status === "needs_review" ? "warn" : "ok"
      );
      await loadAll();
    } catch (e) {
      setError((e as Error).message);
      log(`Upload failed: ${(e as Error).message}`, "warn");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo">◆</div>
          <div>
            <h1>Claims Triage Console</h1>
            <p>Four-layer claims assistant — LLM → RAG → Agent → MCP</p>
          </div>
        </div>
        <div className="meta">
          {health && (
            <>
              <span className="chip mono" title={Object.entries(health.prompts)
                .map(([k, v]) => `${k}: ${v}`)
                .join("\n")}>
                {health.model} · prompts v{health.prompts.triage_system?.split("+")[0] ?? "?"}
              </span>
              <span className="chip mono">threshold {threshold.toFixed(2)}</span>
              <span className="chip mono">{health.retrieval} retrieval</span>
              <span className={`dot ${health.has_api_key ? "ok" : "warn"}`}>
                {health.has_api_key ? "LLM connected" : "no API key"}
              </span>
            </>
          )}
          <button className="ghost" onClick={loadAll}>
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="banner">
          {error}
          <button className="ghost" onClick={() => setError(null)}>
            dismiss
          </button>
        </div>
      )}

      <section
        className={`card ingestbar ${uploading ? "busy" : ""}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files?.[0];
          if (f) handleUpload(f);
        }}
      >
        <div className="ingest-left">
          <div className="dz-icon">⇪</div>
          <div>
            <h2>Ingest a document</h2>
            <p className="hint">
              Drag a file here — PDF · DOCX · XLSX · CSV · TXT · PNG/JPG · text or scanned (vision OCR)
            </p>
          </div>
        </div>
        <div className="ingest-right">
          {lastUpload && (
            <div className="upload-result compact">
              <div className="upline mono">
                {lastUpload.claim.claim_id} · {lastUpload.extraction.format.toUpperCase()} ·{" "}
                {lastUpload.extraction.mode === "vision" ? "OCR" : "text"} ·{" "}
                {lastUpload.extraction.attempts} attempt(s) ·{" "}
                <b>{lastUpload.extraction.status}</b>
                {lastUpload.extraction.duplicate_of
                  ? ` · duplicate of ${lastUpload.extraction.duplicate_of}`
                  : ""}
              </div>
            </div>
          )}
          <label className={`filebtn ${uploading ? "disabled" : ""}`}>
            {uploading ? "Extracting…" : "Choose file"}
            <input
              type="file"
              accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.png,.jpg,.jpeg"
              disabled={uploading || !health?.has_api_key}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
                e.currentTarget.value = "";
              }}
            />
          </label>
        </div>
      </section>

      <section className="stats">
        <div className="stat">
          <span className="label">Claims in scope</span>
          <b>{stats.total}</b>
        </div>
        <div className="stat">
          <span className="label">Flagged for review</span>
          <b className="hi">{stats.flagged}</b>
        </div>
        <div className="stat">
          <span className="label">In adjuster queue</span>
          <b className="hi">{stats.routed}</b>
        </div>
        <div className="stat">
          <span className="label">Pass-through rate</span>
          <b className="ok">{stats.passRate}%</b>
        </div>
        <div className="stat">
          <span className="label">Override rate</span>
          <b className={queueStats?.override_rate ? "hi" : ""}>
            {queueStats?.override_rate == null
              ? "—"
              : `${Math.round(queueStats.override_rate * 100)}%`}
          </b>
          <span className="sub mono">
            {queueStats ? `${queueStats.accepted} accepted · ${queueStats.overridden} overridden` : ""}
          </span>
        </div>
        <div className="stat">
          <span className="label">LLM spend</span>
          <b>{usage ? `$${usage.cost_usd.toFixed(4)}` : "—"}</b>
          <span className="sub mono">
            {usage ? `${usage.calls} calls · ${usage.input_tokens + usage.output_tokens} tok` : ""}
          </span>
        </div>
      </section>

      <main className="grid">
        <section className="card claims">
          <div className="card-head">
            <h2>Claims</h2>
            <div className="head-actions">
              <span className="hint">Click a row to inspect · risk is deterministic rules</span>
              <button className="primary" onClick={triageAll} disabled={batching || !health?.has_api_key}>
                {batching ? "Triaging…" : "Run full triage"}
              </button>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Claim</th>
                <th>Status</th>
                <th className="num">Amount</th>
                <th className="num">Expected cost</th>
                <th>Review risk</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {claims.map((c) => {
                const pct = Math.max(4, Math.round(c.risk.score * 100));
                return (
                  <tr
                    key={c.claim_id}
                    className={selected === c.claim_id ? "selected" : ""}
                    onClick={() => setSelected(c.claim_id)}
                  >
                    <td>
                      <span className="mono strong">{c.claim_id}</span>
                      {c.source && <span className="ubadge mono">UPLOAD</span>}
                      {c.duplicate_of && (
                        <span className="dbadge mono" title={`possible duplicate of ${c.duplicate_of}`}>
                          DUP {c.duplicate_of}
                        </span>
                      )}
                      <div className="sub">
                        {c.claim_type} · {c.region} · {c.claimant}
                      </div>
                    </td>
                    <td>
                      <span className={`status ${c.status}`}>{c.status.replace("_", " ")}</span>
                    </td>
                    <td className="num mono">{money(c.claimed_amount)}</td>
                    <td className="num mono">
                      {c.expected_cost ? (
                        <span
                          title={`${c.expected_cost.cluster_label} cluster · model MAE ${money(
                            c.expected_cost.model_mae
                          )}${
                            c.expected_cost.drivers[0]
                              ? ` · top driver: ${c.expected_cost.drivers[0].feature}`
                              : ""
                          }`}
                        >
                          {money(c.expected_cost.predicted_severity)}
                          <div className="sub">
                            C{c.expected_cost.cluster} · {c.expected_cost.cluster_label}
                          </div>
                        </span>
                      ) : (
                        <span className="sub">—</span>
                      )}
                    </td>
                    <td>
                      <div className="riskcell">
                        <div className="bar">
                          <i
                            style={{
                              width: `${pct}%`,
                              background: c.risk.flagged ? "var(--danger)" : "var(--ok)",
                              opacity: c.risk.score === 0 ? 0.25 : 1,
                            }}
                          />
                        </div>
                        <span className={`score ${c.risk.flagged ? "hi" : "lo"}`}>
                          {c.risk.score.toFixed(2)}
                        </span>
                        {c.risk.borderline && (
                          <span className="mini-chip" title={c.risk.borderline.note}>
                            borderline
                          </span>
                        )}
                      </div>
                      <div className="signals">
                        {c.risk.signals.length
                          ? c.risk.signals.slice(0, 3).map((s) => s.signal).join(" · ")
                          : "no signals"}
                      </div>
                    </td>
                    <td className="actions">
                      <button
                        className="mini"
                        disabled={!!busy[c.claim_id] || !health?.has_api_key}
                        onClick={(e) => {
                          e.stopPropagation();
                          triage(c.claim_id);
                        }}
                      >
                        {busy[c.claim_id] ? "…" : "Triage"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <aside className="card queue">
          <div className="card-head">
            <h2>Adjuster queue</h2>
            <button className="ghost" onClick={resetQueue}>
              Clear
            </button>
          </div>
          {queue.length === 0 && <p className="empty">No claims routed yet.</p>}
          {queue.map((e) => (
            <div className="qentry" key={e.claim_id}>
              <div className="qhead">
                <span className="mono strong">{e.claim_id}</span>
                <span className={`score ${e.risk_score >= threshold ? "hi" : "lo"}`}>
                  {e.risk_score.toFixed(2)}
                </span>
              </div>
              {e.summary_headline && <p className="qsummary">{e.summary_headline}</p>}
              <p className="qreason">{e.reason}</p>
              {e.model_context && (
                <div className="qmodel mono">
                  expected {money(e.model_context.predicted_severity)} · C{e.model_context.cluster}{" "}
                  {e.model_context.cluster_label}
                </div>
              )}
              <div className="qfoot mono">
                {e.risk_signals.length} signals · {e.assigned_to} ·{" "}
                {new Date(e.routed_at).toLocaleTimeString()}
              </div>
              {e.human_decision ? (
                <div className={`decision ${e.human_decision}`}>
                  {e.human_decision}
                  {e.decision_reason ? ` · ${e.decision_reason}` : ""}
                </div>
              ) : (
                <div className="qbtns">
                  <button className="mini ok" onClick={() => decide(e.claim_id, "accepted")}>
                    Accept
                  </button>
                  <button className="mini danger" onClick={() => decide(e.claim_id, "overridden")}>
                    Override
                  </button>
                </div>
              )}
            </div>
          ))}
        </aside>

        <section className="card ask">
          <div className="card-head">
            <h2>Policy question</h2>
            <span className="hint">
              grounded in {selectedClaim ? `${selectedClaim.claim_type}/${selectedClaim.region}` : "…"} policy
              · citations or refusal
            </span>
          </div>
          <div className="askrow">
            <span className="chip mono">{selectedClaim?.claim_id ?? "select a claim"}</span>
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ask()}
              placeholder="Ask about coverage, deductibles, deadlines…"
            />
            <button className="primary" onClick={ask} disabled={asking || !health?.has_api_key}>
              {asking ? "Retrieving…" : "Ask"}
            </button>
          </div>
          {askResult && (
            <div
              className={`answer ${
                askResult.answer.startsWith("REFUSED") ||
                askResult.answer.startsWith("INSUFFICIENT")
                  ? "refused"
                  : ""
              }`}
            >
              {(askResult.answer.startsWith("REFUSED") ||
                askResult.answer.startsWith("INSUFFICIENT")) && (
                <div className="refusal-tag">Refused — no grounded answer</div>
              )}
              <p>{askResult.answer}</p>
              <div className="cites">
                {askResult.citations.map((c) => (
                  <span className="cite mono" key={c}>
                    {c}
                  </span>
                ))}
                {askResult.citations.length === 0 && (
                  <span className="sub">no citations — escalated, not answered from memory</span>
                )}
              </div>
            </div>
          )}
        </section>

        <section className="card activity">
          <div className="card-head">
            <h2>Activity</h2>
            {usage && (
              <span className="hint mono">
                {usage.calls} calls · {usage.input_tokens + usage.output_tokens} tokens · $
                {usage.cost_usd.toFixed(4)}
              </span>
            )}
          </div>
          {activity.length === 0 && <p className="empty">Actions will appear here.</p>}
          {activity.map((a) => (
            <div className={`act ${a.tone}`} key={a.id}>
              <span className="mono time">{a.time}</span>
              <span>{a.text}</span>
            </div>
          ))}
        </section>
      </main>

      <footer className="foot mono">
        LLM · RAG · Agent · MCP &nbsp;|&nbsp; risk rules are deterministic &nbsp;|&nbsp; the agent routes,
        never decides
      </footer>
    </div>
  );
}
