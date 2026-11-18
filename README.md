# Incident Triage Agent with Human Approval

An AI-assisted incident triage system with a strict human-in-the-loop decision requirement. An autonomous agent investigates filed incidents by searching past precedents in PostgreSQL, drafts structured triage proposals (severity, category, summary, recommended action, confidence), validates outputs against deterministic safety rules and a critic pass, and presents the audit trail to an engineer for approval or rejection.

**Zero auto-approval**: every decision is recorded with the triage run for full auditability.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   React + TypeScript (Vite)                 │
│         - Sign-in Screen (JWT Header: Authorization)        │
│         - Incident List (Status, Severity, Category)        │
│         - Detail View: Agent Stepper, Proposal, Decisions   │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP REST (Bearer JWT)
┌──────────────────────────────▼──────────────────────────────┐
│                  FastAPI Backend (Async)                    │
│    - /auth/login (JWT token issuance via bcrypt + HS256)    │
│    - /incidents (Protected incident CRUD)                   │
│    - /incidents/{id}/triage (Executes agent loop)           │
│    - /incidents/{id}/decision (Atomic transaction write)    │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
┌──────────────▼──────────────┐ ┌─────────────▼───────────────┐
│     PostgreSQL (Docker)     │ │        Triage Agent         │
│  - users                    │ │  1. Plan (query synthesis)  │
│  - incidents                │ │  2. Act (Postgres search)   │
│  - triage_runs              │ │  3. Draft (Groq / Stub)     │
│  - decisions                │ │  4. Check (Pydantic retry)  │
│  - alembic_version          │ │  5. Rule Check (Safety)     │
└─────────────────────────────┘ │  6. Critic (Contradictions) │
                                │  7. Stop (Human Review)     │
                                └─────────────────────────────┘
```

---

## 2. Technical Decisions & Core Concepts ("The Why")

* **How JWT Auth Works Here:**  
  When an engineer signs in with email and password (`POST /auth/login`), the server verifies the bcrypt hash and issues a signed JSON Web Token (HS256). Every subsequent request includes this token in the `Authorization: Bearer <token>` header. FastAPI decodes the token and extracts the user identity without needing a database session lookup on each call.

* **Why the Alembic Migration Exists:**  
  Schema changes are versioned as numbered Python migration files (`001_initial_schema.py`, `002_triage_runs_and_decisions.py`). This guarantees reproducible, testable, and automated database construction from scratch across development, testing, and production environments.

* **What the Transaction Protects:**  
  In `POST /incidents/{id}/decision`, inserting the human `Decision` record and updating the `Incident.status` (to `Approved` or `Rejected`) execute inside a single atomic database transaction. If any failure occurs during commit, both writes roll back completely, preventing orphaned decisions or untracked status modifications.

* **What the Agent Loop's Check Step Catches:**  
  LLMs can hallucinate keys, output malformed syntax, or violate category/severity enums. The check step uses Pydantic v2 to strictly validate `{severity, category, summary, recommended_action, confidence}` before any downstream logic proceeds.

* **What Happens on Bad JSON:**  
  If the model returns invalid JSON, the check step captures the exact error and executes **one retry**, passing the validation error back to the model. If the second response remains invalid, it immediately raises a descriptive `TriageError` rather than guessing.

* **Dual Engine Implementation:**  
  The agent loop is implemented in two forms:
  1. **Framework-Free Loop** (`app/agent/loop.py`): Pure Python async control flow with explicit retry loops and deterministic safety guards.
  2. **LangGraph State Machine** (`app/agent/graph.py`): Re-implemented using a `StateGraph` with explicit nodes (`plan`, `tools`, `draft`, `check`, `rule_check`, `critic`) and conditional retry edges. Both can be toggled via `?engine=framework_free` or `?engine=langgraph`.

---

## 3. Synthetic Data Declaration

> [!IMPORTANT]
> **ALL DATA IN THIS REPOSITORY IS SYNTHETIC.**
> Every incident, user account, equipment identifier, and failure scenario was written synthetically for learning and evaluation purposes.
> - **Zero real company data**
> - **Zero Renault telemetry or proprietary data**
> - **Zero real personal data**
> All seed records are explicitly prefixed with `[SYNTHETIC]`.

---

## 4. Scaffolding & Code Provenance

No full-stack template or scaffolding framework (such as the Full Stack FastAPI Template) was used. All backend models, routers, migrations, agent loops, LangGraph state machines, tests, and frontend React components were built from scratch for this project.

---

## 5. How to Run

### Prerequisites
- Python 3.12+
- Node.js 20+
- Docker Desktop (running)

### Step 1: Environment Configuration
Create a `.env` file in the root directory (based on `.env.example`):
```bash
DATABASE_URL=postgresql+asyncpg://triage_user:triage_pass@localhost:5432/triage_db
JWT_SECRET_KEY=generate_a_secure_random_key_here
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# Optional: Groq API Key for live hosted model calls
GROQ_API_KEY=
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=openai/gpt-oss-20b
```

### Step 2: Start PostgreSQL Database
```bash
docker compose up -d postgres
```

### Step 3: Backend Setup & Migrations
```bash
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # Windows PowerShell

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Seed synthetic test data
python scripts/seed.py
```

### Step 4: Run the Backend API
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
*(Or run the complete stack via Docker Compose: `docker compose up --build`)*

### Step 5: Run the Frontend
```bash
cd frontend
npm install
npm run dev
```
Open your browser at `http://localhost:5173`.

### Synthetic Demo Login Credentials:
- **Email:** `engineer@vessel-demo.example.com`
- **Password:** `demo-pass-1`

---

## 6. Running Tests

### Backend Unit & Integration Tests (pytest)
Runs 34+ comprehensive tests covering all slices, 200/400/401/404 HTTP codes, deterministic safety rules, validation retries, and atomic transaction rollback:
```bash
pytest tests/ -v
```

### End-to-End Browser Tests (Playwright)
Runs the complete browser flow: signing in, filing an incident, running the agent triage loop, inspecting steps, and approving:
```bash
cd frontend
npx playwright test
```
