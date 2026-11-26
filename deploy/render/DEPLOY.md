# Deploying to Render (free, no ports, no DNS setup)

This is the option to use when the home network cannot accept inbound connections.
Render builds the existing `Dockerfile`, gives you `https://<name>.onrender.com` with a
managed certificate, and needs nothing from your router.

## Why it fits

| Constraint | Value |
|---|---|
| App memory footprint | **141 MB** measured (`docker stats`) |
| Render free plan | **512 MB**, 0.1 CPU |
| Cost | **$0** — free web service, 750 instance-hours/month |
| Build minutes | 500/month free; this build takes ~3 minutes |
| Bandwidth | 5 GB/month included |
| Inbound ports needed | **none** |

The trade-off: free services **sleep after 15 minutes idle**, and the first request then takes
about 50 seconds. That is acceptable for a portfolio link if you warn people; it is not
acceptable for an interview where you expect someone to click at a random moment.

## Deploy

1. Sign in at <https://dashboard.render.com> with GitHub (this also grants repo access).
2. **New → Blueprint**.
3. Pick the `insurance-claims-analytics` repository. Render finds `render.yaml` at the root.
4. When prompted, paste `OPENAI_API_KEY`. Everything else is already in the blueprint.
5. Click **Apply**. The Docker build runs; the service goes live on
   `https://claims-console.onrender.com` (the exact host is shown in the dashboard).

Redeploys happen automatically on every push to `main` (`autoDeploy: true`).

## Verify

```bash
curl -s https://claims-console.onrender.com/api/health | jq '.demo, .retrieval'
curl -s https://claims-console.onrender.com/api/metrics/summary | jq '.headline'
curl -s -o /dev/null -w "%{http_code}\n" https://claims-console.onrender.com/dashboard
```

Expected: demo guards on, `retrieval: hybrid`, dashboard `200`.

The local script works against the hosted URL too:

```powershell
.\scripts\verify_demo.ps1 https://claims-console.onrender.com/
```

## Free-plan behaviour to plan around

| Symptom | Cause | Mitigation |
|---|---|---|
| ~50 s first load | free instance sleeping | open the URL before sharing it; mention "first load takes a minute" |
| Data resets | ephemeral filesystem | expected: queue, ledger and traces are per instance |
| Build fails on memory | free build machine is small | it fits today at ~141 MB runtime; the build is the Docker build, not the app |
| Slow streaming | 0.1 CPU | the claims app is request/response, not streaming |

## Custom domain

You *can* attach `claims-demo.duckdns.org` to Render, but DuckDNS only manages A records;
Render needs a CNAME. Leave the `onrender.com` hostname — it is more reliable than the DuckDNS
setup and nothing in the resume depends on the name.

## QuenchGuard

Do **not** deploy it publicly as-is:

- it has no authentication and no spend cap, and a chat request runs an agent loop on your key;
- 357 MB of the 512 MB free plan leaves little headroom for a chat burst;
- it keeps human-in-the-loop state in-process, so a restart loses pending approvals.

Keep QuenchGuard as the screen-share/local demo (it is already running on
`127.0.0.1:8002`), or add basic auth and a spend cap before exposing it. The interactive
`reports/project_walkthrough.html` is the public artifact for that project.

## If Render asks for a card

That is workspace verification, not a charge; free compute stays $0. If you would rather not
add one, the alternatives in order of effort are:

1. **Cloud Run** (`../cloudrun/`) — card required, but 2–5 s cold start and $0 at demo traffic.
2. **Hugging Face Spaces** — PRO ~$9/month for Docker Spaces.
3. **Local + tunnel** (`../local-demo/`) — no account, but URLs change and your ISP blocks
   `*.trycloudflare.com`.
