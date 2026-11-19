# ---- web build (React + Vite) ----
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# ---- runtime (FastAPI + built UI) ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY src ./src
COPY prompts ./prompts
COPY data ./data
COPY eval ./eval
COPY models ./models
RUN pip install --no-cache-dir -e . --no-deps

COPY --from=web /web/dist ./frontend/dist

EXPOSE 8000
CMD ["uvicorn", "claims_assistant.api:app", "--host", "0.0.0.0", "--port", "8000"]
