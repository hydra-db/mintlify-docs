# syntax=docker/dockerfile:1

# ── build ─────────────────────────────────────────────────────────────────────
FROM rust:1-slim AS build
WORKDIR /app
# Cache deps independent of source
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY tests ./tests
RUN cargo build --release --bin askai-gateway

# ── run: distroless-slim static image (~5 MB) ─────────────────────────────────
FROM gcr.io/distroless/cc-debian12
COPY --from=build /app/target/release/askai-gateway /askai-gateway
EXPOSE 8080
USER nonroot
ENTRYPOINT ["/askai-gateway"]
