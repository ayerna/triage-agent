FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make the startup script executable
RUN chmod +x scripts/start.sh

# Render sets $PORT dynamically; start.sh normalises DATABASE_URL,
# runs Alembic migrations, seeds, then starts uvicorn.
CMD ["bash", "scripts/start.sh"]
