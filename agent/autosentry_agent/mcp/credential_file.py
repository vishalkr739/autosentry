import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path

_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@contextmanager
def CredentialFile(tenant_id: str, value: str, base_dir: Path):
    """Write `value` to a tmpfs-backed file, yield its path, and delete
    the file on exit regardless of how the `with` block exits (spec
    section 2.3).

    `tenant_id` becomes part of the filename, so it must match
    `^[A-Za-z0-9_-]+$` (no path separators, `..`, or other characters that
    could escape `base_dir` or produce an unexpected path)."""
    if not _TENANT_ID_RE.fullmatch(tenant_id):
        raise ValueError(
            f"Invalid tenant_id {tenant_id!r}: must match {_TENANT_ID_RE.pattern}"
        )
    base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(base_dir, 0o700)

    filename = f"{tenant_id}-{secrets.token_hex(8)}"
    path = base_dir / filename
    fd = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)

    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def sweep_stale_credential_files(base_dir: Path) -> None:
    if not base_dir.exists():
        return
    for entry in base_dir.iterdir():
        if entry.is_file():
            entry.unlink(missing_ok=True)
