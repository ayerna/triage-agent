"""
Seed script: inserts synthetic vessel and equipment maintenance incidents.

ALL DATA IS SYNTHETIC. Generated for learning purposes only.
No real company data, no real vessel data, no Renault data.
Run after 'alembic upgrade head':
    python scripts/seed.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.config import settings
from app.models import Incident, SeverityEnum, StatusEnum, User

# ---------------------------------------------------------------------------
# SYNTHETIC SEED DATA — not real, not Renault, written for this learning project
# ---------------------------------------------------------------------------
SEED_USERS = [
    {"email": "engineer@vessel-demo.local", "password": "demo-pass-1"},
    {"email": "supervisor@vessel-demo.local", "password": "demo-pass-2"},
]

SEED_INCIDENTS = [
    # ── Already-resolved incidents (give the agent precedent to find) ──────
    {
        "title": "[SYNTHETIC] Main Engine Lube Oil Pressure Drop",
        "description": (
            "[SYNTHETIC] Lube oil pressure on main engine ME-01 dropped from 4.2 bar "
            "to 2.1 bar over 20 minutes during transit at 12 knots. "
            "Standby pump activated automatically. No bearing temperature rise detected. "
            "Root cause: clogged suction strainer. Strainer cleaned, pressure restored."
        ),
        "severity": SeverityEnum.High,
        "status": StatusEnum.Approved,
    },
    {
        "title": "[SYNTHETIC] Bilge Alarm — Engine Room Forward",
        "description": (
            "[SYNTHETIC] High-bilge alarm triggered in engine room forward section. "
            "Water level at 35 cm. Bilge pump P-03 started manually. "
            "Leak traced to a corroded pipe flange on auxiliary cooling water line. "
            "Flange replaced, bilge cleared. Incident closed."
        ),
        "severity": SeverityEnum.Medium,
        "status": StatusEnum.Approved,
    },
    {
        "title": "[SYNTHETIC] Radar Antenna Bearing Failure",
        "description": (
            "[SYNTHETIC] X-band radar antenna on the bridge stopped rotating. "
            "Faulty motor bearing replaced by electronics officer. "
            "System operational within 4 hours. Backup S-band radar used during repair."
        ),
        "severity": SeverityEnum.Medium,
        "status": StatusEnum.Approved,
    },
    {
        "title": "[SYNTHETIC] Cargo Hold Ventilation Fan Failure — Hold 3",
        "description": (
            "[SYNTHETIC] Ventilation fan F-03A for cargo hold 3 tripped on overload. "
            "Fan motor insulation resistance below 1 MΩ. Motor rewound at next port. "
            "Cargo temperature monitoring increased during repair period."
        ),
        "severity": SeverityEnum.Low,
        "status": StatusEnum.Approved,
    },
    # ── Open / Triaged incidents (the ones the agent will work on) ─────────
    {
        "title": "[SYNTHETIC] Fuel Injector Fault — Cylinder 4",
        "description": (
            "[SYNTHETIC] Exhaust temperature on cylinder 4 of main engine ME-01 "
            "is running 45°C above average. Fuel injector suspected faulty. "
            "Engine load reduced to 70% MCR. Injector replacement parts on order."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] GMDSS Battery Charger Fault",
        "description": (
            "[SYNTHETIC] GMDSS battery charger unit BC-02 showing fault LED. "
            "Charger output voltage 0 V. Batteries on GMDSS-01 at 90% capacity. "
            "Backup charger BC-01 connected. Manufacturer contacted for guidance."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Steering Gear Hydraulic Oil Leak",
        "description": (
            "[SYNTHETIC] Small hydraulic oil leak detected on steering gear unit SG-02 "
            "port side ram seal. Oil drip rate approximately 2 drops per minute. "
            "Absorbent pads placed. Vessel speed reduced to 8 knots pending assessment."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Fire Detection Panel Fault — Accommodation Deck A",
        "description": (
            "[SYNTHETIC] Fire detection panel zone 4 (Accommodation Deck A) showing "
            "open-circuit fault. Detector heads in cabins A-04 through A-09 not responding. "
            "Fire watch posted on affected zone. Wiring inspection ongoing."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Sewage Treatment Plant Alarm",
        "description": (
            "[SYNTHETIC] STP-01 sewage treatment plant high-level alarm active. "
            "Effluent quality sensor reading turbidity above discharge limits. "
            "Discharge valve closed. Holding tank at 60% capacity."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Lifeboat Davit Wire Corrosion",
        "description": (
            "[SYNTHETIC] During weekly inspection, corrosion found on lifeboat 2 "
            "port davit wire at sheave contact point. Wire cross-section reduction "
            "estimated 15%. Lifeboat 2 placed out of service pending wire replacement."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Network Switch Failure — Bridge Integrated System",
        "description": (
            "[SYNTHETIC] Network switch SW-BRG-01 connecting ECDIS, AIS, and VDR "
            "went offline at 03:42 UTC. ECDIS lost AIS overlay. Backup switch "
            "installed. VDR recording interrupted for 12 minutes."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Emergency Generator Coolant Leak",
        "description": (
            "[SYNTHETIC] Coolant leak detected on emergency generator EG-01 at "
            "the cylinder head gasket. Coolant level dropped 2 litres in 24 hours. "
            "Generator operational but under close monitoring."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Safety Valve Lifting — Boiler B-02",
        "description": (
            "[SYNTHETIC] Safety valve on boiler B-02 lifted at 8.4 bar (set point 8.0 bar). "
            "Steam pressure rose due to crew error in feedwater control. "
            "SAFETY concern: repeated lifting may damage valve seat. "
            "Boiler load reduced, crew briefed on procedures."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] Compressed Air Leaks — Main Air Receiver",
        "description": (
            "[SYNTHETIC] Multiple small leaks detected on main air receiver AR-01 drain "
            "valve and associated pipework. Compressor running at 70% duty cycle "
            "vs normal 30%. Leak repairs scheduled for next port call."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
    {
        "title": "[SYNTHETIC] CCTV System Offline — Cargo Deck",
        "description": (
            "[SYNTHETIC] Four CCTV cameras on cargo deck (CD-01 to CD-04) offline "
            "following a power surge during port operations. DVR unit damaged. "
            "Manual security watch posted. Replacement DVR ordered."
        ),
        "severity": None,
        "status": StatusEnum.Open,
    },
]


async def seed():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Create users
        created_users = []
        for u in SEED_USERS:
            user = User(email=u["email"], hashed_password=hash_password(u["password"]))
            session.add(user)
            created_users.append(user)

        await session.flush()  # get user IDs before inserting incidents

        # Create incidents (all owned by first user)
        for inc_data in SEED_INCIDENTS:
            inc = Incident(
                title=inc_data["title"],
                description=inc_data["description"],
                severity=inc_data["severity"],
                status=inc_data["status"],
                created_by=created_users[0].id,
            )
            session.add(inc)

        await session.commit()
        print(f"✅ Seeded {len(SEED_USERS)} users and {len(SEED_INCIDENTS)} synthetic incidents.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
