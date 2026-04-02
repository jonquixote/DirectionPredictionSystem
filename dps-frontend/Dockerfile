# Build multi-stage
FROM node:20-alpine as builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --legacy-peer-deps
COPY . .
RUN npm run build

# Serve stage
FROM nginx:alpine
# Copy our custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Copy the built artifacts
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
