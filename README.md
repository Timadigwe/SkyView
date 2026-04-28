# SkyView

SkyView is a small full-stack app for **natural-language questions about Solana** using a hosted LLM ([OpenRouter](https://openrouter.ai/)) and a **read-only** Model Context Protocol (MCP) server that talks to the chain. The backend applies **input and output guardrails** before and after the agent runs tool calls. Optional **conversation persistence** uses JSON on disk locally or a **private S3 bucket** when deployed on AWS.

The UI is a **static-export Next.js** app; production hosting is **S3 website hosting**. The API runs on **AWS App Runner** from a **Docker** image stored in **ECR**.

## Repository layout

| Path | Role |
|------|------|
| `backend/` | FastAPI app (`backend.main:app`), chat + SSE, MCP stdio client, memory store |
| `solana-mcp-minimal/` | Node MCP server (`@solana/kit`): balances, token data, and other read-only tools—built to `build/index.js` |
| `frontend/` | Next.js 15 (`output: "export"`), dashboard chat UI |
| `terraform/` | ECR, App Runner, two S3 buckets (static site + private chat memory), GitHub OIDC IAM |
| `scripts/deploy.sh` | Terraform apply (two-phase when App Runner is not in state yet), Docker push, App Runner deployment, `next build`, `aws s3 sync` |
| `.github/workflows/` | `deploy.yml` (push to `main` / `master`), `destroy.yml` (manual, gated) |

## How it fits together

1. At startup the API spawns the MCP server over stdio and registers its tools.  
2. `POST /api/chat` (or `/api/chat/stream` for streamed text) runs guardrails, then an agent loop that calls MCP tools over the live session, then output guardrails again.  
3. If persistence is on, each browser session id maps to a transcript in `data/memory/` (local) or in the Terraform-created chat-memory bucket (AWS, with `USE_S3=true` and instance role credentials).  
4. The dashboard calls `/api/status`, subscribes to `/api/events` (SSE) for live connection, and uses the streaming chat endpoint. Session ids are kept in `localStorage` and history is restored via `GET /api/chat/history/{session_id}` when the backend can read storage.

## Local development

**Prerequisites:** Python **3.12+**, [uv](https://github.com/astral-sh/uv), **Node 20+** and npm.

1. **Environment**  
   Copy `.env.example` to `.env` in the **repository root** and/or under `backend/` (the backend loads `.env` from its process working directory—if you start uvicorn from `backend/`, place or link `.env` there). Set at least `OPENROUTER_API_KEY` and `NEXT_PUBLIC_API_URL` (e.g. `http://127.0.0.1:8000` for the frontend).

2. **Build the MCP server** (required before the API can serve chat):

   ```bash
   cd solana-mcp-minimal
   npm ci
   npm run build
   ```

3. **Run the API** (from `backend/` after `uv sync`):

   ```bash
   cd backend
   uv sync
   uv run uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
   ```

   Health check: `GET /api/health` (returns `ok` and where conversation storage is pointed).

4. **Run the frontend**:

   ```bash
   cd frontend
   npm ci
   npm run dev
   ```

   Open the dev server URL (default [http://localhost:3000](http://localhost:3000)). CORS allows localhost on port 3000 by default (see `cors_origin_regex` in `backend/src/backend/settings.py`).

**Docker (API only):** from the repo root, `docker build -f backend/Dockerfile .` — the image installs Node, builds `solana-mcp-minimal`, and runs uvicorn on port 8000.

## Configuration (summary)

All variables are documented in [`.env.example`](.env.example). Notable ones:

- **LLM:** `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, optional `GUARDRAIL_MODEL`  
- **Chain:** `SOLANA_RPC_URL` (default devnet)  
- **Memory:** `PERSIST_CONVERSATIONS`, and for S3: `USE_S3`, `S3_BUCKET`, `AWS_REGION`  
- **Frontend build:** `NEXT_PUBLIC_API_URL` must be the public HTTPS App Runner URL in production (set automatically by `scripts/deploy.sh` when building)

Terraform variables (region, `openrouter_*`, `solana_rpc_url`, `create_app_runner`, `github_repository`, state bucket names, etc.) live in `terraform/variables.tf`.

## AWS and CI

- **First-time / local Terraform:** `terraform` in `terraform/` can use **local state** if you pass `-backend=false` on `init` (as `deploy.sh` does when `TF_STATE_BUCKET` is unset). For GitHub Actions, remote state uses a bucket such as `skyview-terraform-state-<account_id>` unless `TF_STATE_BUCKET` is set.  
- **Deploy script:** `./scripts/deploy.sh <environment>` runs Terraform (skippable with `SKIP_TERRAFORM=1`), builds and pushes `linux/amd64` to ECR, optionally applies again to create App Runner, starts a deployment, builds the frontend with `NEXT_PUBLIC_API_URL` set to the App Runner URL, and syncs `frontend/out/` to the static site bucket.  
- **GitHub:** repository secrets **AWS_ROLE_ARN** (OIDC role from Terraform output `github_actions_role_arn`) and **OPENROUTER_API_KEY** are required for `deploy.yml`. Optional: `TF_STATE_BUCKET`, `TF_STATE_REGION`.  
- **Destroy:** `.github/workflows/destroy.yml` is **workflow_dispatch** only and requires typing `destroy` in the confirmation field.

After apply, `terraform output` shows the static website URL, ECR URL, App Runner URL, and chat memory bucket name.

## API reference (short)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Liveness and storage mode |
| `POST` | `/api/chat` | Full chat turn (JSON body with `message`, `history`, optional `session_id`) |
| `POST` | `/api/chat/stream` | Same logic, SSE chunks |
| `GET` | `/api/chat/history/{session_id}` | Load persisted messages |
| `GET` | `/api/status` | MCP / status payload for the dashboard |
| `GET` | `/api/events` | SSE stream of thoughts and status (used by the UI) |
| `GET` | `/api/thoughts`, `/api/rebalances` | Recent log slices (handy for debugging) |

## License

No license file is present in this repository; add one if you intend to distribute or collaborate.
