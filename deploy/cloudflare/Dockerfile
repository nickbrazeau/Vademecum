# The seat: Vademecum's away node as one container image (ADR 0017).
#
# Stage 1 builds the web app (the dashboard the gateway serves). Stage 2 is
# the runtime: the API and the MCP gateway in one virtualenv, and seat.py
# supervising (snapshots go to the Worker's bucket). No macOS frameworks here, so there is
# no on-device page reading on the seat; that stays the Mac's.

FROM node:22-slim AS web
WORKDIR /src/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
COPY apps/mcp/src/vademecum_mcp/widgets /src/apps/mcp/src/vademecum_mcp/widgets
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY apps/api /app/apps/api
COPY apps/mcp /app/apps/mcp
COPY --from=web /src/apps/mcp/src/vademecum_mcp/widgets/app.html /app/apps/mcp/src/vademecum_mcp/widgets/app.html
RUN pip install /app/apps/api /app/apps/mcp
COPY --from=web /src/apps/web/dist /app/web
COPY deploy/cloudflare/seat.py /app/seat.py

# Records live here between snapshots; the volume is the container's own.
ENV VADEMECUM_DATA_DIR=/data VADEMECUM_MCP_DESK_DIST=/app/web
VOLUME ["/data"]
EXPOSE 8766
CMD ["python", "/app/seat.py", "run"]
