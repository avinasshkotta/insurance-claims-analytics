export type RiskSignal = {
  signal: string;
  weight: number;
  description: string;
  evidence: string;
};

export type Risk = {
  score: number;
  flagged: boolean;
  borderline?: {
    borderline: boolean;
    band: [number, number];
    decision: string;
    note: string;
  } | null;
  signals: RiskSignal[];
};

export type ExpectedCost = {
  predicted_severity: number;
  cluster: number;
  cluster_label: string;
  drivers: { feature: string; impact: number }[];
  model_mae: number;
};

export type Claim = {
  claim_id: string;
  claim_type: string;
  region: string;
  status: string;
  claimed_amount: number;
  claimant?: string;
  channel?: string;
  incident_date?: string;
  reported_date?: string;
  risk: Risk;
  expected_cost?: ExpectedCost | null;
  duplicate_of?: string | null;
  source?: {
    filename: string;
    format: string;
    chars: number;
    uploaded_at: string;
  } | null;
};

export type QueueEntry = {
  claim_id: string;
  reason: string;
  risk_score: number;
  risk_signals: RiskSignal[];
  summary_headline?: string;
  model_context?: {
    predicted_severity: number;
    cluster: number;
    cluster_label: string;
    drivers: { feature: string; impact: number }[];
  } | null;
  assigned_to: string;
  status: string;
  human_decision?: "accepted" | "overridden" | null;
  decision_reason?: string | null;
  decided_at?: string | null;
  routed_at: string;
};

export type QueueStats = {
  total: number;
  pending: number;
  accepted: number;
  overridden: number;
  override_rate: number | null;
};

export type Usage = {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
};

export type Health = {
  ok: boolean;
  model: string;
  threshold: number;
  prompts: Record<string, string>;
  has_api_key: boolean;
};

export type TriageResult = {
  claim_id: string;
  final: string;
  tool_log: { tool: string; ok: boolean; args: Record<string, unknown> }[];
  risk: Risk;
  queue_entry: QueueEntry | null;
  usage: Usage;
};

export type AskResult = {
  claim_id: string;
  claim_type: string;
  region: string;
  answer: string;
  citations: string[];
  retrieved: number;
};

export type UploadResult = {
  claim: Claim;
  extraction: {
    format: string;
    chars: number;
    notes: string;
    attempts: number;
    issues: string[];
    status: string;
    mode: "text" | "vision";
    duplicate_of?: string | null;
  };
  run_id: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),
  claims: () => request<{ claims: Claim[]; threshold: number }>("/api/claims"),
  queue: () => request<{ entries: QueueEntry[]; stats: QueueStats }>("/api/queue"),
  usage: () => request<Usage>("/api/usage"),
  triage: (id: string) =>
    request<TriageResult>(`/api/triage/${encodeURIComponent(id)}`, { method: "POST" }),
  ask: (claim_id: string, question: string) =>
    request<AskResult>("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim_id, question }),
    }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadResult>("/api/uploads", { method: "POST", body: form });
  },
  resetQueue: () => request<{ ok: boolean }>("/api/queue/reset", { method: "POST" }),
  decide: (claim_id: string, decision: "accepted" | "overridden", reason?: string) =>
    request<{ entry: QueueEntry; stats: QueueStats }>(
      `/api/queue/${encodeURIComponent(claim_id)}/decision`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, reason }),
      }
    ),
};
