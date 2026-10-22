FROM node:24-alpine
WORKDIR /app
COPY package*.json ./
# Exactly what package-lock.json pins; fails if it is out of sync with package.json.
RUN npm ci
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
