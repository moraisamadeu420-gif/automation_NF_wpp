"""
app/utils/file_utils.py
"""
import uuid
from pathlib import Path

from app.core.config import settings


def save_bytes_to_temp(data: bytes, suffix: str = ".jpg") -> Path:
    """Write raw bytes to a temp file under output/screenshots/."""
    path = settings.screenshots_path / f"tmp_{uuid.uuid4().hex}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def cleanup_file(path: Path | str | None) -> None:
    target = Path(path) if path else None
    if target and target.exists():
        try:
            target.unlink()
        except OSError:
            pass
