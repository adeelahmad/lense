# Matterbridge (https://github.com/42wim/matterbridge, Apache-2.0) for Lens's chat rooms: the optional `matterbridge`
# service in Docker Compose, configured from Settings → Chat rooms (fastapi_backend/app/domain/matterbridge.py).
# Built with WhatsApp (multi-device), which the upstream image leaves out: it uses whatsmeow (MPL-2.0) and
# go.mau.fi/libsignal (GPL-3.0), so this image, a separate program Lens only talks to over HTTP, is GPL-3.0 as a whole.
FROM golang:1.24-alpine AS build
ARG MATTERBRIDGE_VERSION=v1.26.0
ARG TAGS=whatsappmulti,nomsteams
RUN CGO_ENABLED=0 GOBIN=/out go install -tags "$TAGS" -trimpath -ldflags "-s -w" \
      github.com/42wim/matterbridge@${MATTERBRIDGE_VERSION}

FROM alpine:3.20
RUN apk --no-cache add ca-certificates mailcap
COPY --from=build /out/matterbridge /usr/local/bin/matterbridge
COPY run.sh /usr/local/bin/lens-matterbridge
VOLUME /matterbridge
EXPOSE 4242
ENTRYPOINT ["/usr/local/bin/lens-matterbridge"]
