ARG BRIDGE_IMAGE=ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v35@sha256:5cc36c7f2d1cfc08632f6d6e88cd38c009138915069fc0de282c3a43c60de86e
ARG GO_IMAGE=golang:1.26.4-trixie@sha256:68b7145ec43d1820b9a56704554b53d1520aa2a15cb5233e374188a31b2a1bce
FROM ${BRIDGE_IMAGE} AS bridge

FROM ${GO_IMAGE}
ENV GOTOOLCHAIN=local GOMAXPROCS=2 GOFLAGS=-p=2 GOMEMLIMIT=2GiB
WORKDIR /src
COPY upstream.tar.gz upstream.sha256 /tmp/upstream/
RUN cd /tmp/upstream && sha256sum -c upstream.sha256 && \
    tar -xzf upstream.tar.gz -C /src --strip-components=1

# Compile against the actual WhatsMeow sources shipped in v35, including its patches.
# No production volumes, databases, environment files or linked-device state are used.
COPY --from=bridge /venv/lib/python3.13/site-packages/slidge_whatsapp/go.mod /opt/bridge-go.mod
COPY --from=bridge /venv/lib/python3.13/site-packages/slidge_whatsapp/vendor/go.mau.fi/whatsmeow /opt/bridge-whatsmeow/
RUN test "$(awk '/go.mau.fi\/whatsmeow / {print $2}' go.mod)" = \
         "$(awk '/go.mau.fi\/whatsmeow / {print $2}' /opt/bridge-go.mod)" && \
    go mod download && \
    cp "$(go env GOMODCACHE)/go.mau.fi/whatsmeow@$(awk '/go.mau.fi\/whatsmeow / {print $2}' go.mod)/go.mod" /opt/bridge-whatsmeow/go.mod && \
    go mod edit -replace=go.mau.fi/whatsmeow=/opt/bridge-whatsmeow && \
    go mod tidy
COPY canprobe/ /src/canprobe/
COPY codecprobe/ /src/codecprobe/
COPY codec-reference/ /opt/can-codec-reference/
COPY voicecore/ /src/voicecore/
COPY upstream_tests/ /src/internal/voip/call/
COPY wacalls-srtp-auth.patch /tmp/wacalls-srtp-auth.patch
COPY wacalls-lifecycle.patch /tmp/wacalls-lifecycle.patch
COPY wacalls-media.patch /tmp/wacalls-media.patch
COPY instrumentation/call/ /src/internal/voip/call/
COPY instrumentation/transport/ /src/internal/voip/transport/
RUN git apply --unidiff-zero --check /tmp/wacalls-srtp-auth.patch && \
    git apply --unidiff-zero /tmp/wacalls-srtp-auth.patch && \
    git apply --check /tmp/wacalls-lifecycle.patch && git apply /tmp/wacalls-lifecycle.patch && \
    git apply --check /tmp/wacalls-media.patch && git apply /tmp/wacalls-media.patch
RUN gofmt -w canprobe/*.go voicecore/*.go internal/voip/call/*.go && \
    go test -run '^$' ./internal/voip/... ./internal/wa ./canprobe ./codecprobe ./voicecore && \
    go test -race -run '^$' ./internal/voip/... ./internal/wa ./canprobe ./codecprobe ./voicecore && \
    go build -o /usr/local/bin/can-call-probe ./canprobe && \
    go build -o /usr/local/bin/can-codec-probe ./codecprobe
COPY probe-source.sha256 /opt/can-probe-source.sha256
LABEL org.opencontainers.image.title="CAN isolated voice experiment on v35"
CMD ["/usr/local/bin/can-call-probe"]
