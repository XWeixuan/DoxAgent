# syntax=docker/dockerfile:1.7
FROM rust:1.95.0-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler clang cmake pkg-config libssl-dev python3 \
    && rm -rf /var/lib/apt/lists/*
RUN git init /source \
    && git -C /source remote add origin https://github.com/openai/codex.git \
    && git -C /source fetch --depth 1 origin 01fc69f4026735edfdf6789820549727a4867b11 \
    && git -C /source checkout --detach 01fc69f4026735edfdf6789820549727a4867b11
COPY w3-time-awareness-0.159.3.patch /tmp/w3-time-awareness.patch
COPY prepare_build.py /tmp/prepare_build.py
RUN git -C /source apply --check /tmp/w3-time-awareness.patch \
    && git -C /source apply /tmp/w3-time-awareness.patch \
    && python3 /tmp/prepare_build.py normalize /source
WORKDIR /source/codex-rs
ARG CARGO_BUILD_JOBS=2
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/source/codex-rs/target \
    cargo build --locked --release -p codex-cli -p codex-code-mode-host --bins \
    && mkdir -p /output/symbols && cp target/release/codex /output/codex \
    && objcopy --only-keep-debug /output/codex /output/symbols/codex.debug \
    && strip --strip-unneeded /output/codex \
    && objcopy --add-gnu-debuglink=/output/symbols/codex.debug /output/codex \
    && cp target/release/codex-code-mode-host /output/codex-code-mode-host \
    && objcopy --only-keep-debug /output/codex-code-mode-host /output/symbols/codex-code-mode-host.debug \
    && strip --strip-unneeded /output/codex-code-mode-host \
    && objcopy --add-gnu-debuglink=/output/symbols/codex-code-mode-host.debug /output/codex-code-mode-host \
    && cp /source/LICENSE /output/LICENSE.codex \
    && python3 /tmp/prepare_build.py manifest /source /tmp/w3-time-awareness.patch /output
FROM scratch AS artifact
COPY --from=build /output/ /
