# syntax=docker/dockerfile:1

# ---- сборка: статический бинарь без cgo ----
FROM golang:1.27-alpine AS build
WORKDIR /src

# Зависимости отдельным слоем: при изменении только кода слой берётся из кеша.
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download

COPY . .
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server

# ---- финальный образ: distroless без shell и пакетного менеджера, пользователь nonroot ----
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/server /server
EXPOSE 8080
ENV ADDR=:8080
# В образе нет curl — проверку готовности выполняет сам бинарь.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 CMD ["/server", "-healthcheck"]
# exec-форма: бинарь — PID 1 и сам получает SIGTERM от docker stop.
ENTRYPOINT ["/server"]
