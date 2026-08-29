import itertools
import os
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google import genai
from google.genai import types
from pydantic import BaseModel

# --- Setup ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Web search grounding: the model decides per-message whether a search is
# actually needed, so it's safe to leave this on for every chat.
CHAT_CONFIG = types.GenerateContentConfig(
    tools=[types.Tool(google_search=types.GoogleSearch())]
)


# --- Multi-key pool: round-robin with automatic failover ---
#
# GEMINI_API_KEYS: comma-separated list of keys (from as many free accounts
# as you want). Falls back to the single GEMINI_API_KEY var if that's all
# you have. Each request tries the next key in rotation; if a key comes back
# rate-limited (429 / RESOURCE_EXHAUSTED), it's put on a short cooldown and
# the request retries with the next key instead of failing outright.
class KeyPool:
    COOLDOWN_SECONDS = 60  # how long to skip a key after it gets rate-limited

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("No Gemini API keys configured")
        self.clients = [genai.Client(api_key=k) for k in keys]
        self._cooldown_until = [0.0] * len(self.clients)
        self._cycle = itertools.cycle(range(len(self.clients)))
        self._lock = threading.Lock()

    def _next_index(self) -> int:
        with self._lock:
            return next(self._cycle)

    def mark_rate_limited(self, index: int):
        self._cooldown_until[index] = time.time() + self.COOLDOWN_SECONDS

    def available_order(self) -> list[int]:
        """Indices to try this request, starting from the next round-robin
        slot, skipping keys currently on cooldown (unless all are cooling
        down, in which case we try everything anyway)."""
        now = time.time()
        start = self._next_index()
        order = [(start + i) % len(self.clients) for i in range(len(self.clients))]
        ready = [i for i in order if self._cooldown_until[i] <= now]
        return ready or order

    def client(self, index: int):
        return self.clients[index]

    def __len__(self):
        return len(self.clients)


def _load_keys() -> list[str]:
    multi = os.environ.get("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in multi.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("GEMINI_API_KEY")
        if single:
            keys = [single]
    return keys


_keys = _load_keys()
pool = KeyPool(_keys) if _keys else None


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).upper()
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "RATE" in msg and "LIMIT" in msg


# --- In-memory session store ---
# NOTE: lives in process memory only; resets on restart/redeploy and isn't
# shared across multiple Render instances. History is stored as plain
# Content objects (not tied to any one client), so it works no matter which
# key ends up serving a given message.
SESSION_TTL_SECONDS = 60 * 60 * 2  # 2 hours of inactivity
_sessions: dict[str, dict] = {}  # session_id -> {"history": [Content, ...], "last_used": ts}


def _get_history(session_id: str) -> list:
    now = time.time()
    entry = _sessions.get(session_id)
    if entry is not None:
        entry["last_used"] = now
        return entry["history"]

    history: list = []
    _sessions[session_id] = {"history": history, "last_used": now}
    _prune_expired_sessions(now)
    return history


def _prune_expired_sessions(now: float):
    expired = [
        sid for sid, entry in _sessions.items()
        if now - entry["last_used"] > SESSION_TTL_SECONDS
    ]
    for sid in expired:
        del _sessions[sid]


def _extract_sources(response) -> list[str]:
    """Pull grounding source URLs/titles out of a response, if search was used."""
    sources = []
    try:
        candidate = response.candidates[0]
        chunks = candidate.grounding_metadata.grounding_chunks or []
        for chunk in chunks:
            if chunk.web:
                sources.append(chunk.web.title or chunk.web.uri)
    except (AttributeError, IndexError, TypeError):
        pass
    return sources


def _generate_with_rotation(history: list, message: str, tmp_path: str | None):
    """Try the request across the key pool: round-robin order, skipping keys
    on cooldown, failing over to the next key on a rate-limit error. If a
    file is attached, it's (re-)uploaded with whichever key ends up serving
    the request, since uploaded files are scoped to that key's project."""
    if pool is None:
        raise RuntimeError("No Gemini API key(s) configured on the server.")

    last_error = None
    for index in pool.available_order():
        client = pool.client(index)
        try:
            parts = []
            if tmp_path:
                uploaded = client.files.upload(file=tmp_path)
                parts.append(
                    types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type)
                )
            parts.append(types.Part.from_text(text=message))

            contents = history + [types.Content(role="user", parts=parts)]
            response = client.models.generate_content(
                model=MODEL_NAME, contents=contents, config=CHAT_CONFIG
            )
            return response
        except Exception as e:
            last_error = e
            if _is_rate_limit_error(e):
                pool.mark_rate_limited(index)
                continue  # try the next key
            raise  # not a rate-limit issue, don't burn through every key

    raise last_error or RuntimeError("All Gemini API keys are rate-limited. Try again shortly.")


class ResetRequest(BaseModel):
    session_id: str | None = None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/chat")
async def chat(
    message: str = Form(...),
    session_id: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    if pool is None:
        return {"error": "No GEMINI_API_KEY(S) set on the server."}

    session_id = session_id or str(uuid.uuid4())
    history = _get_history(session_id)

    tmp_path = None
    try:
        if file is not None and file.filename:
            suffix = os.path.splitext(file.filename)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name

        response = _generate_with_rotation(history, message, tmp_path)

        # Persist just the text to history (not the uploaded file reference,
        # since a file uploaded under one key's project may not be readable
        # if a later turn gets routed to a different key).
        history.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=response.text)]))

        return {
            "reply": response.text,
            "session_id": session_id,
            "sources": _extract_sources(response),
        }
    except Exception as e:
        return {"error": str(e), "session_id": session_id}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/reset")
async def reset(req: ResetRequest):
    if req.session_id and req.session_id in _sessions:
        del _sessions[req.session_id]
    new_id = str(uuid.uuid4())
    return {"session_id": new_id}


@app.get("/health")
async def health():
    return {"status": "ok", "keys_configured": len(pool) if pool else 0}
