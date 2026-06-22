import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from pytest_mock import MockerFixture

from analecta.api.events import EventBus
from analecta.api.routes.extract import router as extract_router
from analecta.api.routes.system import router as system_router
from analecta.config import AppConfig
from analecta.extraction.assets import AssetDownloader
from analecta.extraction.core import ExtractedContent
from analecta.storage.index import VaultIndex

# ---------------------------------------------------------------------------
# EventBus unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber() -> None:
    bus = EventBus()
    async with bus.subscribe() as q:
        await bus.publish({"type": "test", "id": 1})
        assert q.get_nowait() == {"type": "test", "id": 1}


@pytest.mark.asyncio
async def test_put_nowait_delivers_to_subscriber() -> None:
    bus = EventBus()
    async with bus.subscribe() as q:
        bus.put_nowait({"type": "test"})
        assert q.get_nowait() == {"type": "test"}


@pytest.mark.asyncio
async def test_fanout_reaches_all_subscribers() -> None:
    bus = EventBus()
    async with bus.subscribe() as q1:
        async with bus.subscribe() as q2:
            await bus.publish({"type": "fanout"})
            assert q1.get_nowait() == {"type": "fanout"}
            assert q2.get_nowait() == {"type": "fanout"}


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue() -> None:
    bus = EventBus()
    async with bus.subscribe() as q:
        pass  # context exited — queue removed
    bus.put_nowait({"type": "ghost"})
    assert q.empty()


def test_put_nowait_no_subscribers_is_noop() -> None:
    bus = EventBus()
    bus.put_nowait({"type": "nowhere"})  # must not raise


# ---------------------------------------------------------------------------
# Integration test: open SSE stream, fire POST /extract, receive entry_added
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        cfg = AppConfig(vault_path=tmp_path / "vault")
        index = VaultIndex(cfg.vault_path / "analecta.db")
        app.state.config = cfg
        app.state.index = index
        app.state.event_bus = EventBus()
        app.state.port = 0
        yield
        index.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(extract_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sse_receives_entry_added(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch(
        "analecta.api.routes.extract.extract",
        return_value=ExtractedContent(
            title="Test Article",
            html="<p>Hello</p>",
            url="https://example.com/test",
            source_type="article",
            metadata={},
        ),
    )
    mocker.patch.object(AssetDownloader, "process", return_value="<p>Hello</p>")

    app = _make_app(tmp_path)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None)
    server = uvicorn.Server(cfg)

    loop = asyncio.get_event_loop()
    serve_task = loop.create_task(server.serve())

    while not server.started:  # noqa: ASYNC110 — polling uvicorn internal state, Event not applicable
        await asyncio.sleep(0.01)

    port: int = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    received: list[dict[str, object]] = []
    got_event: asyncio.Event = asyncio.Event()

    async def _read_sse() -> None:
        async with httpx2.AsyncClient() as c:
            async with c.stream(
                "GET", f"{base}/api/v1/system/events", timeout=10.0
            ) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        received.append(json.loads(line[len("data:") :].strip()))
                        got_event.set()
                        return

    sse_task = loop.create_task(_read_sse())
    await asyncio.sleep(0.05)  # let SSE connection subscribe before extract fires

    async with httpx2.AsyncClient() as c:
        r = await c.post(
            f"{base}/api/v1/extract",
            json={"url": "https://example.com/test"},
            timeout=15.0,
        )
    assert r.status_code == 200

    await asyncio.wait_for(got_event.wait(), timeout=5.0)
    await sse_task

    server.should_exit = True
    await serve_task

    assert received
    assert received[0]["type"] == "entry_added"
    assert isinstance(received[0]["id"], int)
