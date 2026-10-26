import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from unstructured.partition.pdf import partition_pdf

app = FastAPI()
lock = Lock()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/general/v0/general")
def partition(files: UploadFile = File(...), strategy: str = Form("hi_res"),
              languages: str = Form("chi_sim,eng"), infer_table_structure: bool = Form(True)):
    if strategy != "hi_res":
        raise HTTPException(422, "Only hi_res is supported")
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "Parser busy")
    try:
        with TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            size = 0
            with source.open("wb") as out:
                while block := files.file.read(1024 * 1024):
                    size += len(block)
                    if size > int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024:
                        raise HTTPException(413, "File too large")
                    out.write(block)
            return [element.to_dict() for element in partition_pdf(
                filename=str(source), strategy="hi_res", languages=languages.split(","),
                infer_table_structure=infer_table_structure)]
    finally:
        lock.release()
