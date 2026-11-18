"""
FastAPI application entry point.
Mounts all routers. CORS is open in dev (tighten for production).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, incidents

app = FastAPI(
    title="Incident Triage Agent API",
    description="AI-assisted incident triage with human approval. All incident data is synthetic.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(incidents.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
