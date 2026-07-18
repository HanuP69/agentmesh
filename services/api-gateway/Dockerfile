FROM node:20-alpine
WORKDIR /app
COPY services/api-gateway/package*.json ./
RUN npm install --omit=dev
COPY services/api-gateway/ .
EXPOSE 8000
CMD ["node", "index.js"]
