FROM node:20-alpine
WORKDIR /app
COPY services/supervisor-service/package*.json ./
RUN npm install --omit=dev
COPY services/supervisor-service/ .
EXPOSE 8010
CMD ["node", "index.js"]
