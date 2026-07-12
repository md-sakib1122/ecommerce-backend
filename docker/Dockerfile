# FastAPI backend image.
# Build context is the REPO ROOT (see docker-compose.yml: context: ..),
# so paths below are relative to the repo root, not this docker/ folder.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

# Install deps first so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime code + migrations + seeders (so `docker compose exec api alembic upgrade head`
# and `docker compose exec api python seeders/seed.py` work inside the container).
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
COPY seeders/ seeders/

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
