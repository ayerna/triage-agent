export interface User {
  id: number;
  email: string;
}

export interface IncidentListItem {
  id: number;
  title: string;
  category: string | null;
  severity: string | null;
  status: "Open" | "Triaged" | "Approved" | "Rejected";
  created_at: string;
  latest_triage_id: number | null;
}

export interface IncidentDetail {
  id: number;
  title: string;
  description: string;
  category: string | null;
  severity: string | null;
  status: "Open" | "Triaged" | "Approved" | "Rejected";
  created_by: number;
  created_at: string;
}

export interface AgentStep {
  step: string;
  details: Record<string, any>;
}

export interface TriageRun {
  id: number;
  incident_id: number;
  proposal_json: string | null;
  steps_json: string | null;
  rule_check_json: string | null;
  critic_json: string | null;
  created_at: string;
}

export interface TriageProposal {
  severity: "Low" | "Medium" | "High" | "Critical";
  category: "Hardware" | "Software" | "Network" | "Safety" | "Other";
  summary: string;
  recommended_action: string;
  confidence: number;
}

export interface RuleCheckResult {
  passed: boolean;
  original_severity: string;
  applied_severity: string;
  forced_by_rule: boolean;
  rule_reason: string | null;
}

export interface CriticResult {
  verdict: "ok" | "flagged";
  contradictions: string[];
}
