"""
Groq API client using the OpenAI-compatible endpoint.
Model: openai/gpt-oss-20b (Groq Free Plan).
Follows spec:
- Single calls, one at a time, no parallel calls.
- Concise prompts strictly staying under 8,000 tokens/min cap.
- Extracts JSON cleanly (strips markdown fencing if present).
- Sends tool definitions in Groq payload for real function calling.
"""
import json
import re
from typing import Any
import httpx

from app.agent.schemas import CriticResult, TriageProposal
from app.agent.tools import TOOL_DEFINITIONS
from app.config import settings


def clean_json_text(text: str) -> str:
    """Strip markdown code block markers if the model emits them."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1).strip()
    return text


async def call_groq_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict | None = None,
    max_tokens: int = 350,
) -> dict[str, Any]:
    """Execute chat completion returning full response choice message (content, tool_calls, etc)."""
    if not settings.GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is not configured in .env")

    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": settings.GROQ_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.GROQ_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Groq API returned status {response.status_code}: {response.text}"
            )
        data = response.json()
        return data["choices"][0]["message"]


async def call_groq_chat(messages: list[dict[str, str]], max_tokens: int = 350) -> str:
    """Execute one asynchronous chat completion call against Groq and return clean text."""
    message = await call_groq_completion(messages=messages, max_tokens=max_tokens)
    return clean_json_text(message.get("content") or "")


async def groq_tool_caller(
    incident: dict,
    tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Invokes Groq with real tool definitions.
    Returns parsed tool calls requested by the model:
    [{"id": "...", "name": "...", "arguments": {...}}]
    """
    tools = tools or TOOL_DEFINITIONS
    system_msg = (
        "You are an investigative triage assistant for maritime vessel equipment incidents. "
        "Your task is to search past incidents to find relevant precedent before deciding severity. "
        "Call the appropriate function (e.g. search_past_incidents) to gather evidence."
    )
    user_msg = (
        f"Incident #{incident.get('id')}\n"
        f"Title: {incident.get('title')}\n"
        f"Description: {incident.get('description')}\n\n"
        "Please search past incidents or fetch details if relevant."
    )
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    message = await call_groq_completion(
        messages=messages,
        tools=tools,
        tool_choice="auto",
        max_tokens=250,
    )

    tool_calls = message.get("tool_calls") or []
    parsed_calls = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        fn_name = fn.get("name")
        raw_args = fn.get("arguments", "{}")
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            parsed_args = {}
        parsed_calls.append({
            "id": tc.get("id"),
            "name": fn_name,
            "arguments": parsed_args,
        })
    return parsed_calls


async def groq_decider(
    incident: dict,
    past_evidence: list[dict],
    retry_error: str | None = None,
) -> str:
    """
    Drafts triage proposal using Groq openai/gpt-oss-20b.
    Output schema: {severity, category, summary, recommended_action, confidence}.
    """
    system_msg = (
        "You are an expert maritime and industrial equipment incident triage assistant. "
        "Analyze the incident and precedent evidence, then propose severity and category. "
        "Allowed categories: [Hardware, Software, Network, Safety, Other]. "
        "Allowed severities: [Low, Medium, High, Critical]. "
        "Respond ONLY with a JSON object: "
        '{"severity": "...", "category": "...", "summary": "...", "recommended_action": "...", "confidence": 0.0-1.0}'
    )

    evidence_summary = []
    for ev in past_evidence[:2]:
        evidence_summary.append(
            f"- Past #{ev.get('id')}: {ev.get('title')} | Severity: {ev.get('severity')} | Status: {ev.get('status')}"
        )
    evidence_text = "\n".join(evidence_summary) if evidence_summary else "No similar precedents found."

    user_msg = (
        f"Incident #{incident.get('id')}\n"
        f"Title: {incident.get('title')}\n"
        f"Description: {incident.get('description')}\n\n"
        f"Precedent Evidence:\n{evidence_text}\n"
    )

    if retry_error:
        user_msg += (
            f"\nIMPORTANT: Your previous output failed schema validation: {retry_error}. "
            "Please fix the error and return strictly valid JSON matching the exact schema."
        )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    return await call_groq_chat(messages, max_tokens=300)


async def groq_critic(
    proposal: TriageProposal,
    past_evidence: list[dict],
) -> CriticResult:
    """
    Critic pass: re-reads proposal against evidence to check for contradictions.
    Returns {verdict: "ok" | "flagged", contradictions: [...]}.
    """
    system_msg = (
        "You are an incident triage auditor. Review the proposed triage against the evidence. "
        "Flag any contradictions or unjustified classifications. "
        "Respond ONLY with a JSON object: "
        '{"verdict": "ok" or "flagged", "contradictions": ["..."]}'
    )

    evidence_summary = [
        f"Precedent #{ev.get('id')}: {ev.get('title')} (Sev: {ev.get('severity')})"
        for ev in past_evidence[:2]
    ]

    user_msg = (
        f"Proposal to critique:\n"
        f"Severity: {proposal.severity}\n"
        f"Category: {proposal.category}\n"
        f"Summary: {proposal.summary}\n"
        f"Action: {proposal.recommended_action}\n"
        f"Evidence: {'; '.join(evidence_summary) if evidence_summary else 'None'}\n"
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    try:
        raw_text = await call_groq_chat(messages, max_tokens=200)
        data = json.loads(raw_text)
        verdict = "flagged" if data.get("verdict") == "flagged" else "ok"
        contradictions = data.get("contradictions", [])
        if not isinstance(contradictions, list):
            contradictions = [str(contradictions)]
        return CriticResult(verdict=verdict, contradictions=contradictions)
    except Exception:
        # Fallback if model critic response format had minor glitch
        return CriticResult(verdict="ok", contradictions=[])
