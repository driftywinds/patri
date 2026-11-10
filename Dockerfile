# syntax=docker/dockerfile:1

FROM node:22-alpine AS web
WORKDIR /app/web
RUN npm i -g pnpm@11
COPY web/package.json web/pnpm-lock.yaml web/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --system --uid 10001 --no-create-home patri

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY --from=web /app/web/dist ./web/dist

USER patri
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=4s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["python", "-m", "server.app"]
