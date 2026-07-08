# Family CFO web dashboard image: build the Angular app, then serve the static
# output with nginx (which also proxies /api to the API container).
FROM node:22-alpine AS build

WORKDIR /app/apps/web

# Install dependencies against the committed lockfile first for layer caching.
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

# The generated API client is already committed under src/app/api-client, so
# the build does not need the shared OpenAPI contract.
COPY apps/web/ ./
RUN npm run build

FROM nginx:alpine
COPY docker/web-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist/web/browser /usr/share/nginx/html
EXPOSE 80
