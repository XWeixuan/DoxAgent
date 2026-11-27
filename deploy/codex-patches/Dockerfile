# syntax=docker/dockerfile:1.7
FROM rust:1.99.1-bookworm@sha256:e6f2cff35d0f309acf8433b4f05878ed77092602c5065cc81801177da0bb3a9c AS build-env
RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler clang cmake pkg-config libssl-dev python3 \
    && rm -rf /var/lib/apt/lists/*
RUN git init /source \
    && git -C /source remote add origin https://github.com/openai/codex.git \
    && git -C /source fetch --depth 1 origin 3d86e43b075adeba2193ec5c22ac92f55afd82e6 \
    && git -C /source checkout --detach 3d86e43b075adeba2193ec5c22ac92f55afd82e6
COPY w3-time-awareness-0.172.0.patch /tmp/w3-time-awareness.patch
COPY prepare_build.py /tmp/prepare_build.py
RUN git -C /source apply --check /tmp/w3-time-awareness.patch \
    && git -C /source apply /tmp/w3-time-awareness.patch \
    && python3 /tmp/prepare_build.py normalize /source
WORKDIR /source/codex-rs
COPY storage-log-policy-0.172.0.patch /tmp/storage-log-policy-0.172.0.patch
RUN git -C /source apply --check /tmp/storage-log-policy-0.172.0.patch \
    && git -C /source apply /tmp/storage-log-policy-0.172.0.patch
# The pinned Codex fork enables V8 pointer-compression+sandbox. Deno's default
# release URL does not publish this pair. Use the same assets and SHA256s as
# MODULE.bazel at 3d86e43b075adeba2193ec5c22ac92f55afd82e6.
RUN mkdir -p /opt/v8-prebuilt \
    && curl -fL --retry 2 https://github.com/openai/codex/releases/download/rusty-v8-v152.2.0/librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz -o /opt/v8-prebuilt/v8.a.gz \
    && curl -fL --retry 2 https://github.com/openai/codex/releases/download/rusty-v8-v152.2.0/src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs -o /opt/v8-prebuilt/binding.rs \
    && echo 'b3b490f2aa809eda7f6ac727f851fb00ce8355be72827577cfaac28c7137dbcd  /opt/v8-prebuilt/v8.a.gz' | sha256sum -c - \
    && echo '3e4d5fecdbe0f98a6017e69d7ca82519a77fb032c5eb5e4cdbecdd3e2ec519d5  /opt/v8-prebuilt/binding.rs' | sha256sum -c -
ENV RUSTY_V8_ARCHIVE=/opt/v8-prebuilt/v8.a.gz
ENV RUSTY_V8_SRC_BINDING_PATH=/opt/v8-prebuilt/binding.rs
FROM build-env AS build
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
