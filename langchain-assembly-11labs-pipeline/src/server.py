"""
FastAPI backend — Austin Trip Voice Agent

Exposes a WebSocket endpoint that feeds binary PCM audio into the
STT > Agent > TTS pipeline and streams JSON events back to the browser.

Audio flow:
    Browser mic (native rate → resampled to 16kHz in browser AudioWorklet)
    → WebSocket binary frames → pipeline → JSON events → browser
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from events import event_to_dict
from pipeline import pipeline

app = FastAPI()

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    async def audio_stream():
        while True:
            try:
                data = await websocket.receive_bytes()
                yield data
            except WebSocketDisconnect:
                return

    try:
        async for event in pipeline.atransform(audio_stream()):
            await websocket.send_json(event_to_dict(event))
    except WebSocketDisconnect:
        pass
