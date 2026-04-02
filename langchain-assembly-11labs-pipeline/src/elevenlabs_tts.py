"""
ElevenLabs Text-to-Speech Streaming

Connects to ElevenLabs' WebSocket streaming API to synthesize speech in real-time.
Opens a fresh WebSocket per turn; closes after isFinal is received.

Input:  Text strings (one per AgentChunkEvent)
Output: TTSChunkEvent (PCM audio bytes at 24kHz)

Reference: https://github.com/langchain-ai/voice-sandwich-demo
"""

import asyncio
import base64
import contextlib
import json
import os
from typing import AsyncIterator, Optional

import websockets
from websockets.client import WebSocketClientProtocol

from events import TTSChunkEvent


class ElevenLabsTTS:
    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: str = "eleven_flash_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        output_format: str = "pcm_24000",
        trigger_generation: bool = False,
    ):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is required")

        self.voice_id = (
            voice_id
            or os.getenv("ELEVENLABS_VOICE_ID")
            or "21m00Tcm4TlvDq8ikWAM"  # default: Rachel
        )
        self.model_id = model_id
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.output_format = output_format
        self.trigger_generation = trigger_generation
        self._ws: Optional[WebSocketClientProtocol] = None
        self._connection_signal = asyncio.Event()
        self._close_signal = asyncio.Event()

    async def send_text(self, text: Optional[str]) -> None:
        """Send a text chunk to ElevenLabs for synthesis."""
        if text is None:
            return

        ws = await self._ensure_connection()

        # Empty string signals end-of-stream to ElevenLabs
        if text == "":
            await ws.send(json.dumps({"text": ""}))
            return

        if not text.strip():
            return

        payload = {
            "text": text,
            "try_trigger_generation": self.trigger_generation,
            "flush": True,
        }
        await ws.send(json.dumps(payload))

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
        """Yield TTSChunkEvents as ElevenLabs streams back audio chunks."""
        while not self._close_signal.is_set():
            _, pending = await asyncio.wait(
                [
                    asyncio.create_task(self._close_signal.wait()),
                    asyncio.create_task(self._connection_signal.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            with contextlib.suppress(asyncio.CancelledError):
                for task in pending:
                    task.cancel()

            if self._close_signal.is_set():
                break

            if self._ws and self._ws.close_code is None:
                self._connection_signal.clear()
                try:
                    async for raw_message in self._ws:
                        try:
                            message = json.loads(raw_message)
                            if "audio" in message and message["audio"] is not None:
                                audio_chunk = base64.b64decode(message["audio"])
                                if audio_chunk:
                                    print(f"[tts] audio chunk {len(audio_chunk)} bytes", flush=True)
                                    yield TTSChunkEvent.create(audio_chunk)
                            if message.get("isFinal"):
                                break  # turn complete — connection will be closed below
                            if "error" in message:
                                print(f"[ElevenLabsTTS] error: {message}")
                                break
                        except json.JSONDecodeError as e:
                            print(f"[ElevenLabsTTS] JSON decode error: {e}")
                            continue
                except websockets.exceptions.ConnectionClosed:
                    print("[ElevenLabsTTS] WebSocket connection closed")
                finally:
                    # Close and null out — next turn will open a fresh connection
                    if self._ws and self._ws.close_code is None:
                        await self._ws.close()
                    self._ws = None

    async def interrupt(self) -> None:
        """Close the current TTS connection mid-stream (allows reconnect on next turn)."""
        if self._ws and self._ws.close_code is None:
            await self._ws.close()
        self._ws = None
        self._connection_signal.clear()

    async def close(self) -> None:
        """Permanently close and stop all reconnection attempts."""
        if self._ws and self._ws.close_code is None:
            await self._ws.close()
        self._ws = None
        self._close_signal.set()

    async def _ensure_connection(self) -> WebSocketClientProtocol:
        if self._close_signal.is_set():
            raise RuntimeError("ElevenLabsTTS: connection attempted after close()")
        if self._ws and self._ws.close_code is None:
            return self._ws

        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream-input"
            f"?model_id={self.model_id}&output_format={self.output_format}"
        )
        self._ws = await websockets.connect(url)

        # BOS message: sends voice settings and authenticates
        bos = {
            "text": " ",
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
            },
            "xi_api_key": self.api_key,
        }
        await self._ws.send(json.dumps(bos))
        self._connection_signal.set()
        return self._ws
