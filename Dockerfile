# 多阶段构建：构建期装 devDeps 编译 TS；运行期零运行时依赖（纯 Node 内置）+ ffmpeg
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json tsconfig.json ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production
RUN apk add --no-cache ffmpeg wget
COPY --from=build /app/dist ./dist
COPY package.json ./
USER node
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://localhost:8787/health || exit 1
CMD ["node", "dist/server.js"]
