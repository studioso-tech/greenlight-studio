# Multi-stage Dockerfile for Greenlight Studio (Full-stack on Google Cloud Run)

# Stage 1: Build Next.js Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install --ignore-scripts
COPY frontend ./
RUN npm run build

# Stage 2: Python Backend & Static Serving
FROM python:3.11-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Copy backend source code & datasets
COPY backend ./backend/

# Copy compiled frontend from Stage 1
COPY --from=frontend-builder /app/frontend/out ./frontend/out/

# Generate dataset if not present
RUN python ./backend/scripts/generate_dataset.py

EXPOSE 8080

# Start unified FastAPI server serving API and Frontend
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
