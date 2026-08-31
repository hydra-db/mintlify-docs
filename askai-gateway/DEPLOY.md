# Deploy `askai-gateway` on EC2 + Cloudflare (`askai.staging.hydradb.com`)

Step-by-step for hosting the Ask-AI answer gateway as a single static binary on a
plain EC2 box and exposing it at `askai.staging.hydradb.com` through Cloudflare.

The gateway is one self-contained binary. It holds the HydraDB + LLM keys
**server-side** (env vars), retrieves from the `hydra_docs`/`docs` collection you
ingested, and streams cited answers to the widget at `POST /docs/ask`. The browser
never sees a secret.

There are two ways to put it behind Cloudflare:

- **Path A — Cloudflare Tunnel (recommended).** No public port, no inbound
  security-group rule, no TLS cert to manage on the box. `cloudflared` dials out to
  Cloudflare and the hostname is served over Cloudflare's edge TLS.
- **Path B — Public IP + DNS + Caddy.** Open 443 to the world, terminate TLS on the
  box with Caddy (auto Let's Encrypt), and proxy the hostname with Cloudflare in
  front. Use this if tunnels are disallowed.

Do **Steps 1–4** for either path, then pick **A** or **B** for Step 5.

---

## 0. Prerequisites

- An EC2 instance (Amazon Linux 2023 or Ubuntu 22.04+), `t3.small` is plenty.
- Access to the `hydradb.com` zone in Cloudflare (to add a DNS record / tunnel).
- The corpus already ingested into HydraDB (`database=hydra_docs`, `collection=docs`).
  See `askai-harness/ingest-docs.mjs` — run it once with `HYDRA_DB_API_KEY` set.
- Two secrets ready:
  - `HYDRA_API_KEY` — a HydraDB key with **query** scope.
  - `OPENROUTER_API_KEY` (or any OpenAI-compatible `LLM_API_KEY`).

---

## 1. Build the binary

```bash
cd askai-gateway
cargo build --release
# → target/release/askai-gateway  (~10 MB)
```

This is a normal dynamically-linked Linux binary whose only real runtime
dependency is **glibc ≥ 2.34**. That's satisfied out of the box by the EC2 images
you'd actually use for staging:

| EC2 base image | glibc | runs this binary? |
| --- | --- | --- |
| Amazon Linux 2023 | 2.34 | ✅ |
| Ubuntu 22.04 / 24.04 | 2.35 / 2.39 | ✅ |
| Debian 12 | 2.36 | ✅ |
| Amazon Linux 2 / Ubuntu 20.04 | 2.26 / 2.31 | ❌ (build in a container — below) |

Check the floor yourself: `objdump -T target/release/askai-gateway | grep -oE
'GLIBC_[0-9.]+' | sort -V | tail -1`.

> **Match the arch.** Build on the same CPU arch as the instance — an `x86_64`
> build for `t3/m5`, an `aarch64` build for Graviton (`t4g/m7g`).

> **Older host, or want one portable image?** Build with the repo's `Dockerfile`
> instead — it compiles in `rust:1-slim` and ships a `~5 MB` distroless image that
> bundles its own glibc and runs anywhere Docker does:
> `docker build -t askai-gateway askai-gateway/`. Then run the container on EC2
> instead of the raw binary (Step 3 becomes `docker run` with `--env-file`).
>
> A fully-static musl build is **not** supported: the `ring` TLS backend segfaults
> under musl-static, so stick with the glibc binary or the container image.

## 2. Post the binary somewhere the box can pull

Any of these — pick one:

```bash
# a) scp straight to the box
scp target/release/askai-gateway ec2-user@<EC2_IP>:/tmp/

# b) upload to S3 and curl it down on the box
aws s3 cp target/release/askai-gateway s3://<your-bucket>/askai-gateway/askai-gateway
#   on the box:  aws s3 cp s3://<your-bucket>/askai-gateway/askai-gateway /tmp/

# c) attach it to a GitHub Release and curl the asset URL on the box
```

## 3. Install the binary and its env on EC2

SSH to the instance, then:

```bash
sudo install -m 0755 /tmp/askai-gateway /usr/local/bin/askai-gateway
sudo useradd --system --no-create-home --shell /usr/sbin/nologin askai || true
sudo install -d -o askai -g askai /etc/askai

# Secrets + config live in one env file, root-only readable.
sudo tee /etc/askai/askai.env >/dev/null <<'ENV'
# ── HydraDB retrieval (server-side only) ──────────────────────────────
HYDRA_API_KEY=sk_live_xxxxxxxxxxxxxxxxxxxxxxxx
HYDRA_DATABASE=hydra_docs
HYDRA_COLLECTION=docs

# ── LLM synthesis (server-side only) ──────────────────────────────────
OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxxxxxxxxxx
LLM_MODEL=openai/gpt-4o-mini

# ── answer shaping / access control ───────────────────────────────────
ASKAI_SITE_URL=https://docs.hydradb.com
ASKAI_ALLOWED_ORIGINS=https://docs.hydradb.com,https://hydradb.com
ASKAI_BIND=127.0.0.1
ASKAI_PORT=8080
ASKAI_RATE_LIMIT_RPM=60
ENV
sudo chmod 600 /etc/askai/askai.env
sudo chown root:root /etc/askai/askai.env
```

> `ASKAI_BIND=127.0.0.1` keeps the port local — only the tunnel/reverse-proxy on
> the box can reach it. For Path B behind Caddy this is exactly right; for Path A
> (tunnel) it's also correct. Never bind `0.0.0.0` with a public security group
> unless Caddy/Cloudflare is the only thing in front.

Create the systemd unit so it starts on boot and restarts on crash:

```bash
sudo tee /etc/systemd/system/askai-gateway.service >/dev/null <<'UNIT'
[Unit]
Description=HydraDB Ask-AI answer gateway
After=network-online.target
Wants=network-online.target

[Service]
User=askai
Group=askai
EnvironmentFile=/etc/askai/askai.env
ExecStart=/usr/local/bin/askai-gateway
Restart=always
RestartSec=2
# hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now askai-gateway
sudo systemctl status askai-gateway --no-pager
```

## 4. Smoke-test locally on the box

```bash
curl -s localhost:8080/healthz
# → {"status":"ok", ...}

curl -sN localhost:8080/docs/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"How do I ingest documents?","mode":"auto"}'
# → a stream of NDJSON: {"type":"sources",...} then {"type":"delta","text":"…"} …
```

If retrieval is empty, re-check `HYDRA_DATABASE`/`HYDRA_COLLECTION` match what you
ingested and that the key has query scope.

---

## 5A. Expose via Cloudflare Tunnel  (recommended)

No open ports, no cert on the box.

```bash
# install cloudflared (Amazon Linux/RHEL rpm shown; use the .deb on Ubuntu)
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm -o cloudflared.rpm
sudo rpm -i cloudflared.rpm

cloudflared tunnel login                       # opens a browser auth for the hydradb.com zone
cloudflared tunnel create askai-staging        # note the Tunnel UUID it prints

# route the hostname to this tunnel (creates the CNAME in Cloudflare DNS for you)
cloudflared tunnel route dns askai-staging askai.staging.hydradb.com
```

Config that maps the hostname → the local gateway:

```bash
sudo install -d /etc/cloudflared
sudo tee /etc/cloudflared/config.yml >/dev/null <<'YML'
tunnel: askai-staging
credentials-file: /root/.cloudflared/<TUNNEL_UUID>.json
ingress:
  - hostname: askai.staging.hydradb.com
    service: http://127.0.0.1:8080
  - service: http_status:404
YML

sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

That's it — Cloudflare now serves `https://askai.staging.hydradb.com` (edge TLS) and
forwards to `127.0.0.1:8080` on the box. Skip to **Step 6**.

## 5B. Expose via public DNS + Caddy  (alternative)

1. **Security group:** allow inbound `443` (and `80` for the ACME challenge) from
   `0.0.0.0/0`. Leave `8080` closed.
2. **Cloudflare DNS:** add an **A** record `askai.staging` → `<EC2 public IP>`,
   **Proxied (orange cloud)**. Set SSL/TLS mode to **Full (strict)**.
3. **Caddy** on the box terminates TLS and reverse-proxies to the gateway:

```bash
# install Caddy (see caddyserver.com/docs/install for your distro), then:
sudo tee /etc/caddy/Caddyfile >/dev/null <<'CADDY'
askai.staging.hydradb.com {
    reverse_proxy 127.0.0.1:8080
}
CADDY
sudo systemctl restart caddy
```

Caddy auto-provisions a Let's Encrypt cert; Cloudflare (Full strict) trusts it.

---

## 6. Verify end to end (public)

```bash
curl -s https://askai.staging.hydradb.com/healthz
curl -sN https://askai.staging.hydradb.com/docs/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is a memory?","mode":"auto"}'
```

The widget already points here — `DEFAULT_ENDPOINT` in `askai.js` is
`https://askai.staging.hydradb.com`. Load the docs site and open Ask AI (⌘I/Ctrl+I):
answers should stream with `[n]` citations that link back into the docs.

CORS is governed by `ASKAI_ALLOWED_ORIGINS`; make sure the docs origin is listed
(the gateway echoes an allowed `Origin` back and rejects others).

## Updating the binary later

```bash
# post the new binary to /tmp/askai-gateway on the box, then:
sudo install -m 0755 /tmp/askai-gateway /usr/local/bin/askai-gateway
sudo systemctl restart askai-gateway
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `curl /healthz` fails locally | `journalctl -u askai-gateway -e` — usually a missing env var (the binary refuses to start without `HYDRA_API_KEY` / `LLM_API_KEY`). |
| 502 from the public URL | gateway not listening on `127.0.0.1:8080`, or tunnel/Caddy pointing at the wrong port. |
| Answers stream but cite nothing | `HYDRA_DATABASE`/`HYDRA_COLLECTION` don't match the ingested corpus, or the key lacks query scope. |
| Browser CORS error | add the docs origin to `ASKAI_ALLOWED_ORIGINS` and restart. |
| Tunnel won't start | `credentials-file` path / UUID in `config.yml` is wrong; re-check `cloudflared tunnel list`. |
