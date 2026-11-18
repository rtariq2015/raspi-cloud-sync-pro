FROM rust:1.100.0-bookworm
RUN rustup target add aarch64-unknown-linux-gnu \
 && apt-get update \
 && apt-get install -y --no-install-recommends gcc-aarch64-linux-gnu libc6-dev-arm64-cross \
 && rm -rf /var/lib/apt/lists/*
ENV CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
RUN rustup component add rustfmt clippy
