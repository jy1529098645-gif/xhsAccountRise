# Multi-stage build:
#   1. node:20-alpine builds the React frontend (TS compile + Vite bundle)
#   2. python:3.13-slim copies the dist + installs Python deps + serves both
#      the FastAPI /api/* surface and the static frontend from a single port.
#
# Why one image instead of two services:
#   - "Render free tier" / "Fly.io free tier" only give one process per app.
#   - Front + back share the same domain → no CORS surprises.
#   - SQLite is local-disk anyway; no separate DB tier.
#
# Build locally:
#   docker build -t academicats-rise .
#   docker run -p 8765:8765 \
#       -e ANTHROPIC_API_KEY=sk-... \
#       -e OPENAI_API_KEY=sk-... \
#       -e DEEPSEEK_API_KEY=sk-... \
#       -v $(pwd)/data:/app/data \
#       academicats-rise
# Then visit http://localhost:8765/  (the FastAPI server also serves the
# frontend assets at /).

# ============= Stage 1: frontend =============
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# tsc + vite build → dist/. Override the Pages base so assets resolve at
# /assets/* under the cloud deploy URL (Pages uses /xhsAccountRise/).
ENV VITE_BASE=/
RUN npm run build

# ============= Stage 2: runtime =============
FROM python:3.13-slim AS runtime

# curl_cffi (optional, for tracking auto-refresh) needs libcurl headers.
# Adding it here means the deployed instance can fetch xhs metrics by URL.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache layer for deps.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir curl_cffi  # optional but lightweight

# App code.
COPY studio/ ./studio/
# Frontend assets from stage 1.
COPY --from=frontend-build /build/dist ./frontend/dist

# Data + libraries volume. Mount-friendly: /app/data persists across restarts.
ENV PYTHONPATH=/app
ENV PYTHONUTF8=1
# Default port (override with PORT for Render/Fly which pass a dynamic one)
ENV PORT=8765

# CORS: allow anything by default; production deploys should pin to their domain.
ENV STUDIO_CORS_ORIGINS=*

EXPOSE 8765

# Apply migrations on cold start, then serve both API + static frontend.
# `studio.api:app` mounts /api/*; we also mount /static -> frontend/dist via
# a startup shim so a fresh container is "open and use" in one URL.
CMD ["sh", "-c", "python -m studio migrate && python -m studio serve --host 0.0.0.0 --port $PORT"]
