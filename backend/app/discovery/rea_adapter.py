"""Calls the REA runner (a thin wrapper around the pinned REA command line tool).

The runner is started as a separate process, without a shell and with a minimal environment. It only
performs static analysis: the uploaded file is passed as an argument and is never executed by the
platform. In a container deployment the process is the sandboxed `rea-runner` container.
"""

import json
import os
import shlex
import shutil
import subprocess  # noqa: S404  (runs the fixed REA runner command, never an uploaded file)
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.uploads import load_upload

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WRAPPER = REPO_ROOT / "rea-runner" / "wrapper.mjs"
_ENV_KEYS = ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "DOCKER_HOST")


class ReaError(Exception):
    """A failed analysis. `kind` is stable (timeout, file_not_found, analysis_failed, runner_unavailable, ...)."""

    def __init__(self, kind: str, message: str, *, details: dict[str, Any] | None = None, command: str | None = None) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}
        self.command = command

    @property
    def code(self) -> str | None:
        """The error code reported by REA itself, when there is one."""
        return self.details.get("code")


@dataclass
class ReaRun:
    command: str
    arguments: list[str]
    rea_version: str | None
    duration_ms: int  # as measured by the runner
    wall_ms: int  # including process start
    result: dict[str, Any]  # the REA Evidence JSON

    @property
    def normalized(self) -> dict[str, Any]:
        return self.result.get("normalized_result", {})

    def command_line(self) -> str:
        return " ".join([self.command, *self.arguments])


@dataclass
class ReaRunner:
    command: list[str]
    timeout_seconds: int = 120
    container_workdir: str | None = None
    grace_seconds: int = 15  # extra time on top of the runner's own limit before the process is killed
    runs: list[ReaRun] = field(default_factory=list)

    def run(self, rea_command: str, file_path: Path, options: list[str] | None = None) -> ReaRun:
        file_arg = f"{self.container_workdir.rstrip('/')}/{file_path.name}" if self.container_workdir else str(file_path)
        command = [token.replace("{workdir}", str(file_path.parent)) for token in self.command]
        argv = [*command, rea_command, file_arg, *(options or [])]
        env = {k: v for k, v in os.environ.items() if k in _ENV_KEYS}
        env["REA_TIMEOUT_SECONDS"] = str(self.timeout_seconds)
        started = time.perf_counter()
        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                timeout=self.timeout_seconds + self.grace_seconds,  # the runner enforces its own limit first
                stdin=subprocess.DEVNULL,
                env=env,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReaError("timeout", f"The analysis did not finish in {self.timeout_seconds} seconds", command=rea_command) from exc
        except OSError as exc:
            raise ReaError("runner_unavailable", f"The REA runner could not be started: {exc.strerror or exc}", command=rea_command) from exc
        wall_ms = round((time.perf_counter() - started) * 1000)

        stdout = completed.stdout.decode("utf-8", errors="replace").strip()
        try:
            envelope = json.loads(stdout.splitlines()[-1]) if stdout else None
        except (json.JSONDecodeError, IndexError):
            envelope = None
        if not isinstance(envelope, dict):
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
            raise ReaError("runner_failed", stderr or f"The REA runner exited with code {completed.returncode}", command=rea_command)

        if not envelope.get("ok"):
            error = envelope.get("error") or {}
            details = _structured_details(error.get("message", ""))
            raise ReaError(
                error.get("kind", "analysis_failed"),
                details.get("message") or error.get("message") or "The analysis failed",
                details=details,
                command=rea_command,
            )
        run = ReaRun(
            command=rea_command,
            arguments=[*(options or [])],
            rea_version=envelope.get("rea_version"),
            duration_ms=int(envelope.get("duration_ms", wall_ms)),
            wall_ms=wall_ms,
            result=envelope["result"],
        )
        self.runs.append(run)
        return run


def _structured_details(message: str) -> dict[str, Any]:
    """REA reports failures as JSON inside the message. Keep the stable fields, drop host paths."""
    try:
        parsed = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: parsed[k] for k in ("code", "category", "message", "retryable") if k in parsed}


def get_rea_runner() -> ReaRunner:
    settings = get_settings()
    command = shlex.split(settings.rea_command, posix=os.name != "nt") if settings.rea_command else ["node", str(DEFAULT_WRAPPER)]
    return ReaRunner(
        command=command,
        timeout_seconds=settings.rea_timeout_seconds,
        container_workdir=settings.rea_container_workdir or None,
    )


@contextmanager
def decrypted_workspace(storage_path: str, filename: str) -> Iterator[Path]:
    """Decrypt a stored upload into a private temporary folder for analysis, and remove it afterwards.

    The `{workdir}` placeholder of a container command is replaced by the caller with this folder.
    """
    workdir = Path(tempfile.mkdtemp(prefix="legacylift-"))
    try:
        os.chmod(workdir, 0o700)
        target = workdir / filename
        target.write_bytes(load_upload(storage_path))
        os.chmod(target, 0o400)
        yield target
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
