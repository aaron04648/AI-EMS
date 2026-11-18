FROM node:22-alpine
WORKDIR /app
COPY fetch-data.mjs package.json ./
# Data folder belongs to the node user (copied into the volume on first start)
RUN mkdir -p /srv/data && chown node:node /srv/data
ENV OUT_DIR=/srv/data \
    INTERVAL_MINUTES=180
USER node
CMD ["node", "fetch-data.mjs", "--loop"]
