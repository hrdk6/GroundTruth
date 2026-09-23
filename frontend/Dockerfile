# Frontend image. Node 22 to match the toolchain the app is developed against.
FROM node:22-alpine

WORKDIR /app

# Dependency layer first: it changes far less often than the source.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev"]
