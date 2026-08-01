# ---------- web build ----------
FROM node:22-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN npm run build

# ---------- server ----------
FROM python:3.12-slim
RUN apt-get update -qq \
    && apt-get install -y -q --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY server/ server/
RUN pip install --no-cache-dir -e .

COPY fixtures/ fixtures/
COPY --from=web /app/web/dist web/dist

ENV BSIDE_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000

# single service: API + durable worker + SPA
CMD ["uvicorn", "bside.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
