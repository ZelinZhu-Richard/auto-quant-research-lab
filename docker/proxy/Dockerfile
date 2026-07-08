# Egress-allowlist proxy (PROJECT_BRIEF §7). The lab container lives on an
# internal-only network; this proxy is its sole path out, and it forwards
# CONNECT requests only to the LLM API domains.
FROM alpine:3.20
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter /etc/tinyproxy/filter
EXPOSE 8888
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
