FROM node:20-alpine AS build
WORKDIR /workspace
COPY package.json package-lock.json ./
# The checked-in lockfile is currently missing Angular Material/CDK package
# entries declared by package.json. Resolve the declared dependency manifest
# during the image build rather than silently producing a partial node_modules.
RUN npm install --no-audit --no-fund --package-lock=false
COPY . .
RUN npm run build -- --configuration production

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/dist/fraud-console/browser /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
