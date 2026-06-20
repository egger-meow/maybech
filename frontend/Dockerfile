FROM node:20-alpine

WORKDIR /app

# Install dependencies based on the preferred package manager
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# Next.js telemetry disable
ENV NEXT_TELEMETRY_DISABLED 1

# Build the app
RUN npm run build

EXPOSE 3000

ENV PORT 3000

CMD ["npm", "start"]
