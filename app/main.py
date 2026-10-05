import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, mappings, stats, sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC = Path(__file__).parent / "static"
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MIN_MANUAL_SYNC_GAP = 60  # seconds; YNAB allows 200 requests/hour per token

sync_lock = asyncio.Lock()
last_sync_started = 0.0


async def run_sync() -> None:
    global last_sync_started
    async with sync_lock:
        last_sync_started = time.monotonic()
        await sync.sync_all()


async def sync_loop() -> None:
    while True:
        try:
            await run_sync()
        except Exception:
            log.exception("sync loop")
        await asyncio.sleep(max(5, config.load()["sync_interval_minutes"]) * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    task = asyncio.create_task(sync_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {**stats.meta(), "errors": {k: v for k, v in sync.errors.items() if v},
            "syncing": sync_lock.locked()}


@app.get("/api/summary")
def summary(start: str, end: str, view: str = stats.COMBINED):
    if not (MONTH.match(start) and MONTH.match(end)) or start > end:
        raise HTTPException(400, "start/end must be YYYY-MM with start <= end")
    people = [p["name"] for p in config.load()["people"]]
    if view != stats.COMBINED and view not in people:
        raise HTTPException(404, f"unknown view {view!r}")
    return stats.summary(view, start, end)


@app.post("/api/sync")
async def sync_now():
    if sync_lock.locked():
        return {"started": False, "reason": "already syncing"}
    wait = MIN_MANUAL_SYNC_GAP - (time.monotonic() - last_sync_started)
    if wait > 0:
        return {"started": False, "reason": f"synced moments ago, try again in {int(wait) + 1}s"}
    asyncio.create_task(run_sync())
    return {"started": True}


@app.get("/api/categories")
def categories():
    return stats.category_list()


class Mapping(BaseModel):
    shared: str | None = None


@app.put("/api/categories/{plan_id}/{category_id}")
def map_category(plan_id: str, category_id: str, body: Mapping):
    known = {(c["plan_id"], c["id"]): c for c in stats.category_list()["categories"]}
    cat = known.get((plan_id, category_id))
    if not cat:
        raise HTTPException(404, "unknown category")
    shared = (body.shared or "").strip()[:100]
    # Mapping a category onto its own name is the same as no mapping
    mappings.set_shared(plan_id, category_id, shared if shared and shared != cat["name"] else None)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/categories")
def categories_page():
    return FileResponse(STATIC / "categories.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
