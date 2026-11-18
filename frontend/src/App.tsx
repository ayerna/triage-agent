import React, { useEffect, useState } from "react";
import { api, getToken, removeToken, setToken } from "./api";
import {
  AgentStep,
  CriticResult,
  IncidentDetail,
  IncidentListItem,
  RuleCheckResult,
  TriageProposal,
  TriageRun,
} from "./types";

export default function App() {
  const [token, setAuthToken] = useState<string | null>(getToken());
  const [view, setView] = useState<"list" | "detail">("list");
  const [selectedIncidentId, setSelectedIncidentId] = useState<number | null>(null);

  // Listen for unauthorized events to auto-logout
  useEffect(() => {
    const handleUnauthorized = () => {
      setAuthToken(null);
      setView("list");
    };
    window.addEventListener("auth:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", handleUnauthorized);
  }, []);

  const handleLoginSuccess = (newToken: string) => {
    setToken(newToken);
    setAuthToken(newToken);
    setView("list");
  };

  const handleLogout = () => {
    removeToken();
    setAuthToken(null);
    setView("list");
    setSelectedIncidentId(null);
  };

  if (!token) {
    return <SignInView onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div>
      <header>
        <h1>
          <span>⚓</span> Vessel Incident Triage Agent
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <span style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
            Signed in
          </span>
          <button className="btn btn-secondary" onClick={handleLogout} style={{ padding: "6px 12px", fontSize: "0.85rem" }}>
            Log out
          </button>
        </div>
      </header>

      <main className="container">
        {view === "list" && (
          <IncidentListView
            onSelectIncident={(id) => {
              setSelectedIncidentId(id);
              setView("detail");
            }}
          />
        )}
        {view === "detail" && selectedIncidentId && (
          <IncidentDetailView
            incidentId={selectedIncidentId}
            onBack={() => {
              setView("list");
              setSelectedIncidentId(null);
            }}
          />
        )}
      </main>
    </div>
  );
}

// ── 1. Sign In Screen ────────────────────────────────────────────────────────

function SignInView({ onLoginSuccess }: { onLoginSuccess: (token: string) => void }) {
  const [email, setEmail] = useState("engineer@vessel-demo.example.com");
  const [password, setPassword] = useState("demo-pass-1");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await api.login(email, password);
      onLoginSuccess(res.access_token);
    } catch (err: any) {
      setError(err.message || "Failed to sign in");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-card">
      <h2>Incident Triage Agent</h2>
      <p className="subtitle">Sign in to review and approve incident proposals</p>

      {error && <div className="error-banner">{error}</div>}

      <div className="hint-box">
        <strong>Synthetic Demo Accounts:</strong>
        <div>Email: <code>engineer@vessel-demo.example.com</code></div>
        <div>Password: <code>demo-pass-1</code></div>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="email">Email Address</label>
          <input
            id="email"
            data-testid="login-email"
            className="form-control"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>

        <div className="form-group">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            data-testid="login-password"
            className="form-control"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        <button
          type="submit"
          data-testid="login-submit"
          className="btn btn-primary"
          style={{ width: "100%", marginTop: "8px" }}
          disabled={loading}
        >
          {loading ? "Signing in..." : "Sign In"}
        </button>
      </form>
    </div>
  );
}

// ── 2. Incident List Screen ──────────────────────────────────────────────────

function IncidentListView({ onSelectIncident }: { onSelectIncident: (id: number) => void }) {
  const [incidents, setIncidents] = useState<IncidentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  const fetchIncidents = async () => {
    try {
      const list = await api.listIncidents();
      setIncidents(list);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchIncidents();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim() || !newDesc.trim()) return;
    setCreating(true);
    try {
      const created = await api.createIncident(newTitle, newDesc);
      setNewTitle("");
      setNewDesc("");
      setShowCreate(false);
      await fetchIncidents();
      onSelectIncident(created.id);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <h2 data-testid="incidents-heading" style={{ fontSize: "1.5rem" }}>Incidents</h2>
          <p style={{ color: "var(--text-muted)", fontSize: "0.875rem" }}>
            Synthetic vessel equipment maintenance feed
          </p>
        </div>
        <button
          className="btn btn-primary"
          data-testid="new-incident-btn"
          onClick={() => setShowCreate(!showCreate)}
        >
          {showCreate ? "Cancel" : "+ File New Incident"}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {showCreate && (
        <div className="card" style={{ marginBottom: "24px" }}>
          <h3>File Synthetic Incident</h3>
          <form onSubmit={handleCreate}>
            <div className="form-group">
              <label htmlFor="inc-title">Title</label>
              <input
                id="inc-title"
                data-testid="incident-title-input"
                className="form-control"
                placeholder="e.g. Auxiliary Engine Turbocharger High Temp"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                required
              />
            </div>
            <div className="form-group">
              <label htmlFor="inc-desc">Description</label>
              <textarea
                id="inc-desc"
                data-testid="incident-desc-input"
                className="form-control"
                placeholder="Provide symptoms, readings, equipment identifiers, and safety observations..."
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                required
              />
            </div>
            <button
              type="submit"
              data-testid="incident-submit-btn"
              className="btn btn-success"
              disabled={creating}
            >
              {creating ? "Submitting..." : "Save & Open Incident"}
            </button>
          </form>
        </div>
      )}

      {loading ? (
        <p style={{ color: "var(--text-muted)" }}>Loading incidents...</p>
      ) : (
        <div className="table-card">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Title</th>
                <th>Category</th>
                <th>Severity</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((inc) => (
                <tr
                  key={inc.id}
                  data-testid={`incident-row-${inc.id}`}
                  onClick={() => onSelectIncident(inc.id)}
                >
                  <td style={{ color: "var(--text-muted)", fontWeight: 600 }}>#{inc.id}</td>
                  <td style={{ fontWeight: 600 }}>{inc.title}</td>
                  <td>{inc.category || "—"}</td>
                  <td>
                    {inc.severity ? (
                      <span className={`badge badge-sev-${inc.severity.toLowerCase()}`}>
                        {inc.severity}
                      </span>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>Pending</span>
                    )}
                  </td>
                  <td>
                    <span className={`badge badge-${inc.status.toLowerCase()}`}>
                      {inc.status}
                    </span>
                  </td>
                  <td>
                    <button
                      className="btn btn-secondary"
                      style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectIncident(inc.id);
                      }}
                    >
                      Inspect →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── 3. Incident Detail & Triage Screen ───────────────────────────────────────

function IncidentDetailView({
  incidentId,
  onBack,
}: {
  incidentId: number;
  onBack: () => void;
}) {
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [triageRun, setTriageRun] = useState<TriageRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [triaging, setTriaging] = useState(false);
  const [engine, setEngine] = useState<"framework_free" | "langgraph">("framework_free");
  const [decisionNote, setDecisionNote] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decisionSuccess, setDecisionSuccess] = useState<string | null>(null);

  const fetchDetail = async () => {
    try {
      const data = await api.getIncident(incidentId);
      setIncident(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDetail();
  }, [incidentId]);

  const handleRunTriage = async () => {
    setError(null);
    setTriaging(true);
    try {
      const run = await api.runTriage(incidentId, engine);
      setTriageRun(run);
      await fetchDetail();
    } catch (err: any) {
      setError(err.message || "Triage failed");
    } finally {
      setTriaging(false);
    }
  };

  const handleDecision = async (decision: "Approved" | "Rejected") => {
    setError(null);
    setDeciding(true);
    try {
      await api.recordDecision(incidentId, decision, decisionNote);
      setDecisionSuccess(`Successfully recorded: ${decision}`);
      await fetchDetail();
    } catch (err: any) {
      setError(err.message || "Failed to record decision");
    } finally {
      setDeciding(false);
    }
  };

  if (loading || !incident) {
    return <p style={{ color: "var(--text-muted)" }}>Loading incident #{incidentId}...</p>;
  }

  // Parse JSON payloads from triageRun if available
  const proposal: TriageProposal | null = triageRun?.proposal_json
    ? JSON.parse(triageRun.proposal_json)
    : null;
  const steps: AgentStep[] = triageRun?.steps_json
    ? JSON.parse(triageRun.steps_json)
    : [];
  const ruleCheck: RuleCheckResult | null = triageRun?.rule_check_json
    ? JSON.parse(triageRun.rule_check_json)
    : null;
  const critic: CriticResult | null = triageRun?.critic_json
    ? JSON.parse(triageRun.critic_json)
    : null;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <button className="btn btn-secondary" onClick={onBack}>
          ← Back to Incident List
        </button>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <span className={`badge badge-${incident.status.toLowerCase()}`}>
            Status: {incident.status}
          </span>
          {incident.severity && (
            <span className={`badge badge-sev-${incident.severity.toLowerCase()}`}>
              Severity: {incident.severity}
            </span>
          )}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {decisionSuccess && (
        <div style={{ backgroundColor: "var(--success-bg)", border: "1px solid var(--success)", color: "#86efac", padding: "12px", borderRadius: "8px", marginBottom: "16px" }}>
          {decisionSuccess}
        </div>
      )}

      {/* Incident Information Card */}
      <div className="card" style={{ marginBottom: "24px" }}>
        <h2 style={{ fontSize: "1.4rem", marginBottom: "8px" }}>
          #{incident.id} — {incident.title}
        </h2>
        <p style={{ color: "var(--text-muted)", marginBottom: "16px", whiteSpace: "pre-line" }}>
          {incident.description}
        </p>

        <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <label style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>Triage Engine:</label>
            <select
              className="form-control"
              style={{ width: "auto", padding: "6px 10px", fontSize: "0.85rem" }}
              value={engine}
              onChange={(e: any) => setEngine(e.target.value)}
            >
              <option value="framework_free">Framework-Free Loop</option>
              <option value="langgraph">LangGraph StateGraph</option>
            </select>
          </div>

          <button
            data-testid="run-triage-btn"
            className="btn btn-primary"
            onClick={handleRunTriage}
            disabled={triaging || incident.status === "Approved" || incident.status === "Rejected"}
          >
            {triaging ? "Agent Investigating..." : "⚡ Run Triage Agent"}
          </button>
        </div>
      </div>

      <div className="detail-grid">
        {/* Left Column: Agent Execution Stepper */}
        <div className="card">
          <h3>Agent Investigation Steps</h3>
          {steps.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>
              Click <strong>Run Triage Agent</strong> to investigate this incident against past precedents.
            </p>
          ) : (
            <div className="stepper" data-testid="agent-steps-container">
              {steps.map((st, i) => (
                <div key={i} className="step-item">
                  <div className="step-header">
                    <span>
                      {i + 1}. Step: {st.step}
                    </span>
                  </div>
                  <div className="step-body">
                    {JSON.stringify(st.details, null, 2)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right Column: Proposal, Rules, Critic & Human Decision */}
        <div>
          {proposal && (
            <div className="card" style={{ marginBottom: "24px" }}>
              <h3>Triage Proposal</h3>
              <div style={{ display: "flex", gap: "12px", marginBottom: "12px" }}>
                <div>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", display: "block" }}>PROPOSED SEVERITY</span>
                  <span className={`badge badge-sev-${proposal.severity.toLowerCase()}`}>
                    {proposal.severity}
                  </span>
                </div>
                <div>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", display: "block" }}>CATEGORY</span>
                  <span style={{ fontWeight: 600 }}>{proposal.category}</span>
                </div>
                <div>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", display: "block" }}>CONFIDENCE</span>
                  <span style={{ fontWeight: 600 }}>{(proposal.confidence * 100).toFixed(0)}%</span>
                </div>
              </div>

              <div style={{ marginBottom: "12px" }}>
                <strong style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>Summary:</strong>
                <p style={{ marginTop: "4px" }}>{proposal.summary}</p>
              </div>

              <div>
                <strong style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>Recommended Action:</strong>
                <p style={{ marginTop: "4px" }}>{proposal.recommended_action}</p>
              </div>

              {/* Deterministic Rule Check Notice */}
              {ruleCheck && ruleCheck.forced_by_rule && (
                <div className="alert-rule">
                  <strong>⚠️ Deterministic Safety Rule Enforced:</strong>
                  <div>{ruleCheck.rule_reason}</div>
                </div>
              )}

              {/* Critic Flags Notice */}
              {critic && critic.verdict === "flagged" && (
                <div className="alert-critic">
                  <strong>⚠️ Critic Raised Flags:</strong>
                  <ul>
                    {critic.contradictions.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Human Decision Box */}
          <div className="card">
            <h3>Human Decision</h3>
            <p style={{ fontSize: "0.875rem", color: "var(--text-muted)", marginBottom: "16px" }}>
              Per hard rules: <strong>no incident is auto-approved</strong>. A human engineer must review the proposal and approve or reject.
            </p>

            {incident.status === "Approved" || incident.status === "Rejected" ? (
              <div style={{ padding: "16px", backgroundColor: "rgba(0,0,0,0.2)", borderRadius: "8px", border: "1px solid var(--border-color)" }}>
                <strong>Decision Recorded:</strong> {incident.status}
              </div>
            ) : (
              <div>
                <div className="form-group">
                  <label htmlFor="note">Engineer Note (Optional)</label>
                  <textarea
                    id="note"
                    data-testid="decision-note"
                    className="form-control"
                    placeholder="Provide reasoning or engineering notes for this decision..."
                    value={decisionNote}
                    onChange={(e) => setDecisionNote(e.target.value)}
                    disabled={incident.status !== "Triaged"}
                  />
                </div>

                <div className="decision-actions">
                  <button
                    data-testid="approve-btn"
                    className="btn btn-success"
                    onClick={() => handleDecision("Approved")}
                    disabled={deciding || incident.status !== "Triaged"}
                  >
                    ✓ Approve Triage
                  </button>
                  <button
                    data-testid="reject-btn"
                    className="btn btn-danger"
                    onClick={() => handleDecision("Rejected")}
                    disabled={deciding || incident.status !== "Triaged"}
                  >
                    ✕ Reject Triage
                  </button>
                </div>
                {incident.status === "Open" && (
                  <p style={{ fontSize: "0.8rem", color: "var(--warning)", marginTop: "10px" }}>
                    * Run triage first before recording a decision.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
