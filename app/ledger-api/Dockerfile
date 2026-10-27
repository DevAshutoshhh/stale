# Baseline Node image. Remediation bumps the ARG NODE_IMAGE line only (see .stale/playbooks/anchor-trusted.md).
ARG NODE_IMAGE=node:24.22.1-bookworm-slim
FROM ${NODE_IMAGE}
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY src ./src
COPY deploy ./deploy
ENV NODE_EXTRA_CA_CERTS=/app/deploy/trust/extra-roots.pem \
    STALE_EXCLUDED_SPKI_FILE=/app/deploy/trust/excluded-spki.txt
USER node
CMD ["node", "--require", "./src/trust-policy.cjs", "src/index.js"]
