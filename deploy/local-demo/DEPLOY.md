# Local demo with a real HTTPS URL (DuckDNS + Caddy)

Your AWS account is governed by a service control policy that denies `ecr:CreateRepository`,
so the Lambda-container route is closed there. This setup skips AWS entirely:

```
interviewer ──HTTPS──> yourname.duckdns.org ──> router :443 ──> Caddy (TLS) ──> app :8000
```

Cost: **$0.** DuckDNS is free, Caddy is free, Let's Encrypt is free, and the only compute is
your laptop.

**DuckDNS gives you the name — not TLS and not reachability.** You still need a public IPv4
address and ports 80/443 forwarded. If your ISP uses CGNAT, use the fallback at the bottom.

## Prerequisites

| Requirement | Check |
|---|---|
| Docker Desktop running | `docker info` |
| Public IPv4 address (not CGNAT) | compare `curl ifconfig.me` with your router's WAN IP |
| Router admin access | to forward ports |
| DuckDNS account | duckdns.org, free |

### CGNAT check

If your public IP is in `100.64.0.0/10`, or your router's WAN IP differs from
`curl ifconfig.me`, you are behind CGNAT and port forwarding cannot work. Use the
`cloudflared` fallback instead — it needs no ports.

## 1. Create the DuckDNS name

1. Sign in at duckdns.org → create a subdomain, e.g. `claims-demo`.
2. Copy the **token** from the top of the page.

## 2. Configure this folder

```powershell
cd deploy\local-demo
Copy-Item .env.example .env
notepad .env      # set DOMAIN=claims-demo.duckdns.org and DUCKDNS_TOKEN=...
```

The app's own secrets come from the repo root `.env` (already present).

## 3. Forward the ports

On your router, forward to **this machine's LAN IP**:

| External | Internal | Protocol |
|---|---|---|
| 80 | 80 | TCP |
| 443 | 443 | TCP |

Port 80 is required for the certificate challenge; 443 serves the traffic.

## 4. Open Windows Firewall (admin PowerShell)

```powershell
New-NetFirewallRule -DisplayName "Claims demo HTTP"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow
New-NetFirewallRule -DisplayName "Claims demo HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
```

If IIS or another service already owns port 80, stop it first (`netstat -ano | findstr :80`).

## 5. Start it

```powershell
.\start.ps1
```

The script updates the DuckDNS A record, builds the image, starts Caddy and waits for
`https://<domain>/api/health` to answer. First certificate issuance takes ~30–60 s.

```text
Live:      https://claims-demo.duckdns.org/
Dashboard: https://claims-demo.duckdns.org/dashboard
```

Stop with `docker compose down`. Logs: `docker compose logs -f app caddy`.

## Verify

```powershell
..\..\scripts\verify_demo.ps1 https://claims-demo.duckdns.org/
```

Checks health, demo guards, metrics, dashboard and the uploads 403.

## Before an interview

- Start Docker **10 minutes early**; the first request after a break is slow.
- Visit the URL once yourself so the container and certificate are warm.
- Keep a **90-second screen recording** as backup. A laptop demo can fail on someone
  else's Wi-Fi, and a video cannot.
- Do not put the DuckDNS link on the resume as a permanent portfolio URL — it is dead when
  your laptop is off. Put it in the interview message ("live demo available on request")
  and keep the repo link on the resume.
- Rotate the OpenAI key afterwards if the URL was public for a while.

## Security (this is a public endpoint on your API key)

Already on via `docker-compose.yml`: demo mode, 20 req/min per visitor, $1 daily cap,
uploads disabled, 500-token output ceiling.

Optional extra — require a password for the whole demo: generate a hash and uncomment
`basic_auth` in `Caddyfile`.

```powershell
docker run --rm caddy:2-alpine caddy hash-password --plaintext "your-password"
```

## Fallback: no port forwarding (cloudflared)

If CGNAT or router access blocks you, a free Cloudflare quick tunnel gives you HTTPS
immediately. The URL changes on restart, which is fine for a live interview.

```powershell
winget install Cloudflare.cloudflared
docker compose up -d app          # app only, published on 127.0.0.1:8010
cloudflared tunnel --url http://localhost:8010
```

Use the printed `https://<random>.trycloudflare.com` URL. For a stable hostname without a
domain, `ngrok http 8010` on the free plan includes one static domain.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `start.ps1` times out, Caddy log says `no such host` | DNS not propagated yet, or the A record points elsewhere |
| Caddy log: `connection refused` / timeout on the challenge | port 80 not forwarded or firewall blocking |
| Caddy log: `acme: ... unauthorized` and your IP differs from `curl ifconfig.me` | CGNAT — use the fallback |
| Browser shows the router's admin page | port 80 forwarded to the router, not to this machine |
| `502` from Caddy | app container unhealthy; `docker compose logs app` |
| Works on LAN, not from mobile data | port forwarding or firewall |
| Everything works, then breaks after a day | public IP changed; rerun `duckdns-update.ps1` |
