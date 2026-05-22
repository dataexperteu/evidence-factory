# ── Stage 1: build the SPA ──────────────────────────────────────────────────
FROM node:20-alpine AS spa-builder
WORKDIR /build
COPY ui-app/package*.json ./
RUN npm ci --prefer-offline
COPY ui-app/ ./
RUN npm run build

# ── Stage 2: production runtime ─────────────────────────────────────────────
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./api/
COPY --from=spa-builder /build/dist ./ui-app/dist/

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
