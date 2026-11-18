"""CorelPDFtools - web app that fixes PDFs CorelDRAW won't open.

Run locally:  uvicorn app.main:app --reload
Settings come from environment variables (see README).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .fixer import DEFAULT_PRESET, PRESETS, FixError, check_tools, fix_pdf

# ---------- settings ----------
APP_NAME = os.getenv("APP_NAME", "CorelPDFtools")
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/corelpdftools-jobs"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
FREE_DAILY_LIMIT = int(os.getenv("FREE_DAILY_LIMIT", "3"))  # 0 = no limit
FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", "3600"))  # files deleted after 1 hour
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "2"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "300"))

JOBS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = Path(__file__).parent / "static"
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")

app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
_slots = asyncio.Semaphore(MAX_PARALLEL)

# ---------- free daily limit (in memory; resets on restart - fine for the MVP) ----------
_usage: dict[tuple[str, str], int] = defaultdict(int)
_usage_lock = threading.Lock()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def take_free_slot(ip: str) -> bool:
    if FREE_DAILY_LIMIT <= 0:
        return True
    key = (ip, date.today().isoformat())
    with _usage_lock:
        if _usage[key] >= FREE_DAILY_LIMIT:
            return False
        _usage[key] += 1
        return True


def give_back_slot(ip: str) -> None:
    if FREE_DAILY_LIMIT <= 0:
        return
    key = (ip, date.today().isoformat())
    with _usage_lock:
        _usage[key] = max(0, _usage[key] - 1)


# ---------- cleanup: delete every job folder older than FILE_TTL_SECONDS ----------
async def _cleanup_loop() -> None:
    while True:
        cutoff = time.time() - FILE_TTL_SECONDS
        for d in JOBS_DIR.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except FileNotFoundError:
                pass
        await asyncio.sleep(300)


@app.on_event("startup")
async def _startup() -> None:
    missing = check_tools()
    if missing:
        print(f"WARNING: missing tools: {', '.join(missing)} - conversions will fail")
    asyncio.create_task(_cleanup_loop())


def job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(404, "Not found")
    d = JOBS_DIR / job_id
    if not d.is_dir():
        raise HTTPException(404, "This file has expired. Files are deleted after 1 hour - please upload again.")
    return d


def safe_stem(name: str) -> str:
    stem = Path(name or "file").stem
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" ._") or "file"
    return stem[:80]


# ---------- API ----------
@app.get("/api/health")
def health():
    return {"ok": not check_tools(), "missing_tools": check_tools()}


@app.get("/api/presets")
def presets():
    return {"default": DEFAULT_PRESET,
            "presets": [{"id": k, "label": v["label"]} for k, v in PRESETS.items()],
            "max_upload_mb": MAX_UPLOAD_MB, "free_daily_limit": FREE_DAILY_LIMIT}


@app.post("/api/fix")
async def fix(request: Request, file: UploadFile = File(...),
              preset: str = Form(DEFAULT_PRESET), keep_text: bool = Form(False)):
    ip = client_ip(request)
    if not take_free_slot(ip):
        return JSONResponse(status_code=429, content={
            "error": f"You've used today's {FREE_DAILY_LIMIT} free fixes. Come back tomorrow, or upgrade for unlimited fixes."})

    job_id = uuid.uuid4().hex
    d = JOBS_DIR / job_id
    d.mkdir(parents=True)
    src = d / "input.pdf"
    try:
        # stream to disk with a size cap
        limit = MAX_UPLOAD_MB * 1024 * 1024
        size = 0
        with open(src, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise FixError(f"This file is bigger than {MAX_UPLOAD_MB} MB.")
                out.write(chunk)
        if size == 0:
            raise FixError("The file is empty.")

        async with _slots:
            result = await run_in_threadpool(fix_pdf, src, d, preset, keep_text, MAX_PAGES)
        src.unlink(missing_ok=True)  # keep only what the user downloads

        name = f"{safe_stem(file.filename)}_CorelDRAW.pdf"
        (d / "meta.json").write_text(json.dumps({"name": name, "preset": preset, "keep_text": keep_text}))
        return {
            "job_id": job_id,
            "pages": result.pages,
            "filename": name,
            "download_url": f"/api/jobs/{job_id}/download",
            "before_url": f"/api/jobs/{job_id}/before.png" if result.preview_before else None,
            "after_url": f"/api/jobs/{job_id}/after.png" if result.preview_after else None,
            "expires_in_minutes": FILE_TTL_SECONDS // 60,
        }
    except FixError as e:
        shutil.rmtree(d, ignore_errors=True)
        give_back_slot(ip)  # a failed fix doesn't use up a free slot
        return JSONResponse(status_code=422, content={"error": str(e)})
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        give_back_slot(ip)
        return JSONResponse(status_code=500, content={"error": "Something went wrong on our side. Please try again."})


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    d = job_dir(job_id)
    meta = json.loads((d / "meta.json").read_text())
    return FileResponse(d / "fixed.pdf", media_type="application/pdf", filename=meta["name"])


@app.get("/api/jobs/{job_id}/{which}.png")
def preview(job_id: str, which: str):
    if which not in ("before", "after"):
        raise HTTPException(404, "Not found")
    p = job_dir(job_id) / f"{which}.png"
    if not p.exists():
        raise HTTPException(404, "No preview")
    return FileResponse(p, media_type="image/png")


class Feedback(BaseModel):
    job_id: str
    opened: bool
    corel_version: str | None = None
    note: str | None = None


@app.post("/api/feedback")
def feedback(fb: Feedback):
    if not JOB_ID_RE.match(fb.job_id):
        raise HTTPException(400, "Bad job id")
    row = {"t": int(time.time()), "job_id": fb.job_id, "opened": fb.opened,
           "corel_version": (fb.corel_version or "")[:40], "note": (fb.note or "")[:500]}
    with open(DATA_DIR / "feedback.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    return {"ok": True}


# ---------- website ----------
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
