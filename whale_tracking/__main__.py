"""python -m whale_tracking  →  arranca el servidor en http://localhost:8000"""

import os

import uvicorn


def main() -> None:
    uvicorn.run("whale_tracking.server:app", host=os.environ.get("WT_HOST", "0.0.0.0"),
                port=int(os.environ.get("WT_PORT", "8000")), log_level="info")


if __name__ == "__main__":
    main()
