# Deploying ClearPath Marketing Compliance

One service, one container: Flask serves the API under `/api/*` and the built
React bundle for everything else. SQLite is the database and it is **re-seeded
from `fixtures/` on every boot** — each deploy or restart starts from clean
demo state (10 fixture submissions + the current offer matrix). That is
deliberate: uploads and review decisions do not survive a restart.

The image is defined by the root `Dockerfile` (multi-stage: node builds the
frontend, python runs gunicorn as a non-root user). `deploy/start.sh` is the
entrypoint: seed, then `gunicorn --threads 8 -b 0.0.0.0:$PORT`. The `Procfile`
runs the same script for Procfile-based hosts.

## Prerequisites

- An Anthropic API key (the AI review calls the Claude API).
- Railway CLI (`npm i -g @railway/cli`) — or Fly CLI for the alternative below.
- Docker only if you want to build/run the image locally; Railway and Fly build
  it remotely from the Dockerfile.

## Environment variables (set these on the host)

| Variable | Required | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Claude API key used by the AI review pipeline. |
| `ANTHROPIC_WORKSPACE_ID` | only for identity-linked keys | Sends the workspace header; find it in the Anthropic Console under Settings → Workspaces. |
| `CLEARPATH_DEMO` | demo deployments only | `1` enables the demo reset endpoint so anyone viewing the demo can restore clean state. It lets **any visitor wipe the database** — never set it on a real deployment. |
| `ANTHROPIC_MODEL` | no | Model override for extraction; leave unset for the default. |

`PORT` is injected by the platform; `CLEARPATH_DB` is already set in the image
(`sqlite:////app/data/clearpath.db`) — don't override either.

## Railway (primary)

`railway.json` is checked in (Dockerfile builder, `/api/health` healthcheck,
on-failure restarts), so this is the whole flow:

```sh
railway login
railway init          # create a new project when prompted
railway up            # builds the Dockerfile remotely and deploys
railway variables --set "ANTHROPIC_API_KEY=sk-ant-..." \
                  --set "ANTHROPIC_WORKSPACE_ID=wrkspc_..." \
                  --set "CLEARPATH_DEMO=1"     # demo deployments only — see caveat above
railway domain        # mint the public URL
```

Setting variables triggers a redeploy; when the healthcheck goes green, open
the URL that `railway domain` printed.

## Fly.io (alternative)

`fly.toml` is checked in (`internal_port = 8000` matching the image, warm
machine, `/api/health` check):

```sh
fly launch --no-deploy   # accept the existing fly.toml, pick an app name
fly secrets set ANTHROPIC_API_KEY=sk-ant-... \
                ANTHROPIC_WORKSPACE_ID=wrkspc_... \
                CLEARPATH_DEMO=1     # demo deployments only — see caveat above
fly deploy
fly open
```

## Local production-parity check (no docker needed)

Runs the exact production entrypoint — seed + gunicorn on `$PORT` — against a
throwaway DB, from the repo root (assumes the repo `.venv` exists per README
setup and the frontend has been built):

```sh
(cd frontend && npm ci && npm run build)
PATH="$PWD/.venv/bin:$PATH" CLEARPATH_DB=/tmp/deploycheck.db PORT=5003 bash deploy/start.sh
```

Then in another terminal:

```sh
curl -s http://localhost:5003/api/health                  # {"status":"ok"}
curl -s http://localhost:5003/api/review/submissions      # seeded submissions JSON
curl -s http://localhost:5003/ | head -c 200              # built index.html
```

With docker, the same check against the real image:

```sh
docker build -t clearpath .
docker run --rm -p 5003:8000 clearpath
# then the same three curls against :5003
```

## Post-deploy smoke checklist

1. Open `/` — the review queue renders with the seeded submissions.
2. `curl https://<your-url>/api/health` → `{"status":"ok"}`.
3. Run one AI review end-to-end from the UI (this exercises the Anthropic key;
   an auth failure shows up here, not at boot).
4. Press the demo reset (only present when `CLEARPATH_DEMO=1`) and confirm the
   queue returns to the seeded 10 submissions.
