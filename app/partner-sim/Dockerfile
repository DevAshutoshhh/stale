FROM node:24.22.1-bookworm-slim
WORKDIR /app
COPY package.json server.js ./
# Demo-only certificates, baked in at build time (see certs/README.md).
COPY certs ./certs
USER node
CMD ["node", "server.js"]
