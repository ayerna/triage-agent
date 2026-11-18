import { IncidentDetail, IncidentListItem, TriageRun } from "./types";

const API_BASE = "/api";

export function getToken(): string | null {
  return localStorage.getItem("triage_jwt_token");
}

export function setToken(token: string): void {
  localStorage.setItem("triage_jwt_token", token);
}

export function removeToken(): void {
  localStorage.removeItem("triage_jwt_token");
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> || {}),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    removeToken();
    window.dispatchEvent(new Event("auth:unauthorized"));
    throw new Error("Unauthorized. Please sign in again.");
  }

  if (!response.ok) {
    let errorDetail = `Error ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || JSON.stringify(errJson);
    } catch {
      errorDetail = await response.text();
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export const api = {
  login: async (email: string, password: string): Promise<{ access_token: string }> => {
    return request<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },

  listIncidents: async (): Promise<IncidentListItem[]> => {
    return request<IncidentListItem[]>("/incidents");
  },

  getIncident: async (id: number): Promise<IncidentDetail> => {
    return request<IncidentDetail>(`/incidents/${id}`);
  },

  createIncident: async (title: string, description: string): Promise<IncidentDetail> => {
    return request<IncidentDetail>("/incidents", {
      method: "POST",
      body: JSON.stringify({ title, description }),
    });
  },

  runTriage: async (id: number, engine: "framework_free" | "langgraph" = "framework_free"): Promise<TriageRun> => {
    return request<TriageRun>(`/incidents/${id}/triage?engine=${engine}`, {
      method: "POST",
    });
  },

  recordDecision: async (
    id: number,
    decision: "Approved" | "Rejected",
    note?: string
  ): Promise<any> => {
    return request(`/incidents/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, note: note || null }),
    });
  },
};
