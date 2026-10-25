FROM node:20-alpine
WORKDIR /app
RUN npm init -y >/dev/null && npm install pg@8.13.1 --omit=dev --no-audit --no-fund
COPY index.js .
CMD ["node", "index.js"]
