FROM node:22-alpine@sha256:5b05da3f938f1c604b24c633646d48805a6667f378cb4908438616f3cc48a0bf AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY . .
RUN npm run build

FROM nginx:alpine@sha256:713fc2ebfd92e8217db451477182ee91944b123d2b1ee9fd5a8c6e91ca6c3a11
RUN apk update && apk add --no-cache \
    brotli-libs=1.2.1-r0 \
    curl=8.23.0-r0 \
    libcurl=8.23.0-r0 \
    libexpat=2.10.0-r0 \
    libxslt=1.1.43-r4 \
    zlib=1.4-r0 \
    apk-tools=3.0.9-r0 \
    libapk=3.0.9-r0
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
