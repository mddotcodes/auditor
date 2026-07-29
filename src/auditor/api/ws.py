"""WebSocket job event stream — replay buffer + live subscribe."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from auditor.api.state import AppState
from auditor.contracts.events import JobEvent

logger = logging.getLogger(__name__)


def register_ws(app: FastAPI) -> None:
    @app.websocket("/v1/ws/jobs/{job_id}")
    async def job_events(websocket: WebSocket, job_id: str) -> None:
        state: AppState = websocket.app.state.auditor_state
        # Optional token via query ?token= for browsers; header not always available
        if state.api_token:
            token = websocket.query_params.get("token") or websocket.headers.get("x-api-token")
            auth = websocket.headers.get("authorization")
            if auth and auth.lower().startswith("bearer "):
                token = auth.split(" ", 1)[1].strip()
            if token != state.api_token:
                await websocket.close(code=4401)
                return

        ctx = state.runner.store.get(job_id)
        if ctx is None:
            await websocket.close(code=4404)
            return

        await websocket.accept()
        queue: asyncio.Queue[JobEvent | None] = asyncio.Queue(maxsize=512)
        loop = asyncio.get_running_loop()

        def _on_event(event: JobEvent) -> None:
            def _put() -> None:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop oldest-style: discard this event under backpressure
                    logger.warning("ws backpressure: drop event seq=%s job=%s", event.seq, job_id)

            loop.call_soon_threadsafe(_put)

        # Replay buffered history first
        history = state.runner.bus.history(job_id)
        for event in history:
            if not await _send_event(websocket, event):
                return

        # If already terminal after replay, close
        ctx = state.runner.store.get(job_id)
        if ctx is not None and ctx.status.is_terminal:
            if history and history[-1].terminal is None:
                # ensure client sees terminal even if last buffer event lacked it
                pass
            await websocket.close()
            return

        state.runner.bus.subscribe(job_id, _on_event)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    ctx = state.runner.store.get(job_id)
                    if ctx is not None and ctx.status.is_terminal:
                        # drain any remaining
                        while not queue.empty():
                            ev = queue.get_nowait()
                            if ev is not None and not await _send_event(websocket, ev):
                                return
                        await websocket.close()
                        return
                    # keepalive ping as JSON comment-like heartbeat
                    if websocket.client_state == WebSocketState.CONNECTED:
                        try:
                            await websocket.send_json({"type": "heartbeat", "job_id": job_id})
                        except Exception:
                            return
                    continue

                if item is None:
                    break
                if not await _send_event(websocket, item):
                    return
                if item.terminal is not None:
                    await websocket.close()
                    return
        except WebSocketDisconnect:
            return
        finally:
            state.runner.bus.unsubscribe(job_id, _on_event)


async def _send_event(websocket: WebSocket, event: JobEvent) -> bool:
    """Send one event; return False if connection is dead."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return False
    try:
        payload: dict[str, Any] = event.model_dump(mode="json")
        await websocket.send_json(payload)
        return True
    except Exception:
        return False
