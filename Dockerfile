# syntax=docker/dockerfile:1

FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS web
WORKDIR /app/web
# Pinned: `@11` would silently pick up a new major's install scripts at build time.
RUN npm i -g pnpm@11.28.5
COPY web/package.json web/pnpm-lock.yaml web/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --system --uid 10001 --no-create-home patri

COPY requirements.txt ./
# The file sets --require-hashes, so an unexpected wheel fails the build.
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY --from=web /app/web/dist ./web/dist

LABEL org.opencontainers.image.title="patri" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

USER patri
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=4s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["python", "-m", "server.app"]
