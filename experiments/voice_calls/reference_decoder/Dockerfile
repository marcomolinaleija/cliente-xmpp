# Offline interoperability test only, never part of the production bridge.
FROM rust:1.95.0-trixie@sha256:f49565f188ee00bc2a18dd418183f2c5f23ef7d6e691890517ed341a598f67c3 AS builder
ENV CARGO_BUILD_JOBS=2
WORKDIR /reference
COPY Cargo.toml main.rs build.rs ./
# Unmodified codec from whatsapp-rust d1d929ae; verified archive, not user data.
COPY mlow/ ./mlow/
RUN cargo build --release > /tmp/build.log 2>&1 || (tail -40 /tmp/build.log; exit 1)
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
COPY --from=builder /reference/target/release/can-reference-decoder /usr/local/bin/
COPY --from=builder /reference/Cargo.lock /opt/Cargo.lock
COPY LICENSE.upstream /opt/can-reference-LICENSE
ENTRYPOINT ["/usr/local/bin/can-reference-decoder"]
