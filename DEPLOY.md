# Deploying to Render

## 1. Push this project to a GitHub repo

```
cd gemini-chat
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## 2. Create the service on Render

**Option A — Blueprint (recommended, uses `render.yaml`)**
1. Go to https://dashboard.render.com/blueprints
2. Click **New Blueprint Instance**, connect your GitHub repo, and select it.
3. Render reads `render.yaml` and proposes the `saba-ai` web service automatically.
4. When prompted, paste in your `GEMINI_API_KEYS` value (see below).
5. Click **Apply** — it will build and deploy.

**Option B — Manual**
1. Go to https://dashboard.render.com → **New** → **Web Service**.
2. Connect your repo.
3. Runtime: **Python 3**
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
6. Plan: **Free**
7. Add environment variables (see below), then **Create Web Service**.

## 3. Environment variables

In the Render dashboard, under your service → **Environment**:

| Key | Value |
|---|---|
| `GEMINI_API_KEYS` | `key_one,key_two,key_three,key_four,key_five` (comma-separated, no spaces) |
| `GEMINI_MODEL` | `gemini-2.5-flash` (optional, this is the default) |

If you only have one key, `GEMINI_API_KEY` (singular) also works.

Get keys at https://aistudio.google.com/apikey — one per Google account you're using.

## 4. Done

Render gives you a URL like `https://saba-ai-xxxx.onrender.com`. Open it and chat.

### Notes on the free tier
- The service **spins down after 15 minutes of inactivity** and takes ~30–60s to wake back up on the next request. Fine for personal use; annoying for a public demo people click into cold.
- **Chat memory resets** on every spin-down/redeploy, since it's stored in server RAM (see Phase 2 notes in the code). If you want memory to survive restarts, that's a next step (e.g. a small Postgres or Redis instance — Render has free/cheap options for both).
- Free compute is 512MB RAM / 0.1 CPU — plenty for this app, since Gemini does the heavy lifting remotely.
