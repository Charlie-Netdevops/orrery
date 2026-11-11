"""A tiny fake OpenAI-compatible provider used by the tests. Never talks to the internet.

Models:
  mock-chat            streams a short reply (plus a 'thinking' chunk)
  mock-vision          replies with how many images it received
  mock-echo-key        returns 401 and ECHOES the Authorization header back (tests key redaction)
  mock-ratelimit       returns 429
The expected key is read from MOCK_EXPECTED_KEY (if set); a wrong key gets a 401 that echoes it.
"""
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
MODELS = ["mock-chat", "mock-vision", "mock-echo-key", "mock-ratelimit"]


def _auth_ok(request: Request) -> bool:
    want = os.environ.get("MOCK_EXPECTED_KEY", "")
    return not want or request.headers.get("authorization", "") == f"Bearer {want}"


@app.get("/v1/models")
async def models(request: Request):
    if not _auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]}


@app.get("/healthz")
async def health():
    return {"ok": True}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model")
    auth = request.headers.get("authorization", "")
    if model == "mock-echo-key" or not _auth_ok(request):
        # Deliberately leaky provider: puts the caller's full key in the error body.
        return JSONResponse({"error": {"message": f"Incorrect API key provided: {auth.removeprefix('Bearer ')}",
                                       "header": auth}}, 401)
    if model == "mock-ratelimit":
        return JSONResponse({"error": {"message": "slow down"}}, 429)

    if model == "mock-vision":
        last = body["messages"][-1]["content"]
        n = sum(1 for p in last if isinstance(p, dict) and p.get("type") == "image_url") if isinstance(last, list) else 0
        pieces = [f"I can see {n} image(s)."]
    else:
        pieces = ["Hello ", "from the ", "mock provider."]

    def chunk(delta):
        return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]}) + "\n\n"

    async def gen():
        yield chunk({"reasoning_content": "Thinking about it."})
        for p in pieces:
            yield chunk({"content": p})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
