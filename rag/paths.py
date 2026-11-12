from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = str(Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data"))).resolve())
FAISS_DIR = os.path.join(DATA_DIR, "faiss")
METADATA_PATH = os.path.join(FAISS_DIR, "metadata.pkl")
INDEX_PATH = os.path.join(FAISS_DIR, "index.faiss")
UPLOAD_DIR = os.path.join(DATA_DIR, "upload")
