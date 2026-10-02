# ---------- Build ----------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx ng build --configuration production

# ---------- Runtime ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/dist/web/browser /usr/share/nginx/html
COPY deploy/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
