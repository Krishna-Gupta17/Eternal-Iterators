# One container: builds the React app, then serves it and the API from FastAPI.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY CrunchWeek_StarterPack/ CrunchWeek_StarterPack/
COPY --from=web /web/dist frontend/dist
ENV PORT=8000
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}
