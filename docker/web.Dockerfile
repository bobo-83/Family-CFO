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
# openssl generates the self-signed cert at first start when none is mounted.
RUN apk add --no-cache openssl
COPY docker/web-nginx.conf /etc/nginx/conf.d/default.conf
# Shared body of every HTTPS server block. NOT under conf.d/, which nginx.conf
# includes at http context — a fragment of server-level directives there is a
# parse error.
COPY docker/web-server-common.conf /etc/nginx/family-cfo/server-common.conf
COPY docker/web-entrypoint.sh /usr/local/bin/web-entrypoint.sh
COPY docker/web-render-tailnet-conf.sh /usr/local/bin/web-render-tailnet-conf.sh
RUN chmod +x /usr/local/bin/web-entrypoint.sh /usr/local/bin/web-render-tailnet-conf.sh
# #10: --localize writes one build per locale UNDER browser/
# (dist/web/browser/{en,vi,lt}) — copying dist/web instead put the locales a
# directory too deep and every path 500'd. Copy the browser dir so the locale
# folders land at the nginx root.
COPY --from=build /app/apps/web/dist/web/browser /usr/share/nginx/html
EXPOSE 80 443
CMD ["/usr/local/bin/web-entrypoint.sh"]
