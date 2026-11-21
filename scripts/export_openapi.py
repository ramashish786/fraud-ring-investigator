"""Write docs/api/openapi.json from the FastAPI app (no running services needed).

uv run python scripts/export_openapi.py           # regenerate
uv run python scripts/export_openapi.py --check   # CI: exit 1 if the committed file is stale
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")
os.environ.setdefault("NEO4J_PASSWORD", "unused")
os.environ.setdefault("JWT_SECRET", "unused-for-schema-export-0123456789abcdef")

from app.main import create_app

out = Path("docs/api/openapi.json")
text = json.dumps(create_app().openapi(), indent=2)
if "--check" in sys.argv:
    if not out.exists() or out.read_text() != text:
        print(f"{out} is stale: run `uv run python scripts/export_openapi.py` and commit it", file=sys.stderr)
        sys.exit(1)
    print(f"{out} is up to date")
    sys.exit(0)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(text)
print(f"wrote {out}")
