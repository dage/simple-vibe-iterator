"""Structured JSONL logging utilities and automatic call tracing."""
from __future__ import annotations

import inspect
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
import atexit
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import Any, Dict, Iterable, Mapping, MutableMapping
from uuid import uuid4
import queue

__all__ = [
    "log_tool_call",
    "log_call",
    "auto_log_module",
    "start_auto_logger",
]

# ---- Paths & limits --------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parent
_MODULE_PATH = Path(__file__).resolve()

_LOG_DIR = Path(os.getenv("APP_LOG_DIR", "logs"))
_TOOL_LOG_PATH = Path(os.getenv("TOOL_CALL_LOG", _LOG_DIR / "tool_calls.jsonl"))
_AUTO_LOG_PATH = Path(os.getenv("AUTO_LOG_FILE", _LOG_DIR / "auto_logger.jsonl"))
_LOGGING_ENABLED = os.getenv("APP_ENABLE_JSONL_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}

_TOOL_LOG_MAX_BYTES = 10 * 1024 * 1024
_AUTO_LOG_MAX_BYTES = 100 * 1024 * 1024
_TRIM_RATIO = 0.25  # remove oldest 25% when limit is reached
_AUTO_LOG_MIN_DURATION_MS: float
_auto_log_min_raw = (os.getenv("AUTO_LOGGER_MIN_DURATION_MS") or "").strip()
try:
    _AUTO_LOG_MIN_DURATION_MS = float(_auto_log_min_raw) if _auto_log_min_raw else 1.0
except ValueError:
    _AUTO_LOG_MIN_DURATION_MS = 1.0


# ---- JSON helpers ---------------------------------------------------------

_SENSITIVE_KEYS = {"apikey", "api_key", "token", "secret", "password", "passphrase", "key"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def _truncate_string(text: str, limit: int = 512) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _json_safe(payload: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "...(depth-limit)"

    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return _truncate_string(payload) if isinstance(payload, str) else payload

    if isinstance(payload, Mapping):
        result: Dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if key_str.lower() in _SENSITIVE_KEYS:
                result[key_str] = "***redacted***"
            else:
                result[_truncate_string(key_str)] = _json_safe(value, depth=depth + 1)
        return result

    if isinstance(payload, (list, tuple, set)):
        limited = list(payload)[:25]
        rendered = [_json_safe(item, depth=depth + 1) for item in limited]
        if len(payload) > 25:
            rendered.append(f"...({len(payload) - 25} more)")
        return rendered

    return _truncate_string(repr(payload))


# ---- Generic JSONL logger -------------------------------------------------


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _NullLogger:
    def write(self, payload: Mapping[str, Any]) -> None:
        return


class JsonlLogger:
    """Low-contention JSONL writer that offloads disk IO to a background thread."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int,
        trim_ratio: float,
        sync_writes: bool,
        queue_capacity: int | None = None,
        truncate_interval: int | None = None,
    ) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.trim_ratio = max(0.05, min(0.5, trim_ratio))
        self._sync_writes = sync_writes
        self._queue = queue.Queue(maxsize=queue_capacity or 5000)
        self._stop_event = threading.Event()
        self._writes_since_check = 0
        self._truncate_interval = max(10, truncate_interval or 200)
        self._closed = False
        self._flush_interval = 1 if sync_writes else 20
        self._pending_since_flush = 0
        self._fh = None
        self._writer = threading.Thread(
            target=self._writer_loop,
            name=f"jsonl-writer-{path.name}",
            daemon=True,
        )
        self._writer.start()
        atexit.register(self.close)

    def write(self, payload: Mapping[str, Any]) -> None:
        """Serialize payload and enqueue for async write; drop if queue is full."""
        line = self._serialize(payload)
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            # Drop oldest by removing one item, then enqueue
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._queue.put_nowait(line)
            except queue.Full:
                # queue was refilled by another thread; drop this line
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._writer.join(timeout=1.0)
        self._reset_handle()

    def _serialize(self, payload: Mapping[str, Any]) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False, default=_json_default)
        except Exception:
            return json.dumps(
                {
                    "timestamp": _now_iso(),
                    "event": "log.serialization_error",
                    "error": repr(payload),
                },
                ensure_ascii=False,
            )

    def _writer_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                line = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._append_line(line)
            finally:
                self._queue.task_done()

    def _append_line(self, line: str) -> None:
        try:
            if self._fh is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.path.open("a", encoding="utf-8")
            self._fh.write(line + "\n")
            self._pending_since_flush += 1
            if self._pending_since_flush >= self._flush_interval:
                self._fh.flush()
                if self._sync_writes:
                    os.fsync(self._fh.fileno())
                self._pending_since_flush = 0
        except OSError:
            self._reset_handle()
            return

        self._writes_since_check += 1
        if self._writes_since_check >= self._truncate_interval:
            self._writes_since_check = 0
            self._truncate_if_needed()

    def _truncate_if_needed(self) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return

        if size <= self.max_bytes:
            return

        keep_bytes = int(size * (1 - self.trim_ratio))
        keep_bytes = max(1, keep_bytes)
        tmp_path = self.path.with_suffix(".tmp")
        self._reset_handle()
        try:
            with self.path.open("rb") as src:
                if size > keep_bytes:
                    src.seek(max(0, size - keep_bytes))
                    src.readline()  # align to next newline to avoid partial JSON
                data = src.read()
            with tmp_path.open("wb") as dst:
                dst.write(data)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(tmp_path, self.path)
        except OSError:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _reset_handle(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            fh.flush()
            if self._sync_writes:
                os.fsync(fh.fileno())
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass
        self._pending_since_flush = 0


if _LOGGING_ENABLED:
    _TOOL_LOGGER = JsonlLogger(
        _TOOL_LOG_PATH,
        max_bytes=_TOOL_LOG_MAX_BYTES,
        trim_ratio=_TRIM_RATIO,
        sync_writes=_env_flag("TOOL_LOG_SYNC", True),
        queue_capacity=1024,
        truncate_interval=100,
    )
    _AUTO_LOGGER = JsonlLogger(
        _AUTO_LOG_PATH,
        max_bytes=_AUTO_LOG_MAX_BYTES,
        trim_ratio=_TRIM_RATIO,
        sync_writes=_env_flag("AUTO_LOGGER_SYNC", False),
        queue_capacity=5000,
        truncate_interval=500,
    )
else:
    _TOOL_LOGGER = _NullLogger()
    _AUTO_LOGGER = _NullLogger()


# ---- Tool call logging ----------------------------------------------------


def log_tool_call(*, model: str, tool: str, code: str, output: str) -> None:
    entry: Dict[str, Any] = {
        "timestamp": _now_iso(),
        "model": model,
        "tool": tool,
        "event": "tool_call",
        "code_preview": code.strip()[:200],
        "output": _safe_json_blob(output),
    }
    _TOOL_LOGGER.write(entry)


def _safe_json_blob(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return _truncate_string(raw, 1024)


# ---- Auto-logger (instrumented via sys.setprofile) ------------------------


@dataclass
class CallContext:
    call_id: str
    module: str
    function: str
    filename: str
    lineno: int
    start_ns: int
    parameters: Dict[str, Any] | None


_CALL_STATE: Dict[int, CallContext] = {}
_AUTO_LOGGER_ENABLED = False
_PROFILE_WRAPPER = None


def _should_trace(frame: FrameType) -> bool:
    filename = frame.f_code.co_filename
    if not filename or filename.startswith("<"):
        return False

    try:
        resolved = Path(filename).resolve()
    except Exception:
        return False

    if resolved == _MODULE_PATH:
        return False

    try:
        resolved.relative_to(_SRC_DIR)
        return True
    except ValueError:
        return False


def _capture_parameters(frame: FrameType) -> Dict[str, Any]:
    try:
        arginfo = inspect.getargvalues(frame)
    except Exception:
        return {}

    params: Dict[str, Any] = {}
    for name in arginfo.args or []:
        if name in {"self", "cls"}:
            continue
        params[name] = _json_safe(frame.f_locals.get(name))
    if arginfo.varargs:
        params[arginfo.varargs] = _json_safe(frame.f_locals.get(arginfo.varargs))
    if arginfo.keywords:
        kw = frame.f_locals.get(arginfo.keywords) or {}
        if isinstance(kw, Mapping):
            params.update({str(k): _json_safe(v) for k, v in kw.items()})
    return params


def _profile_dispatch(frame: FrameType, event: str, arg: Any) -> None:
    if event not in {"call", "return", "exception"}:
        return
    if not _should_trace(frame):
        return
    frame_id = id(frame)

    if event == "call":
        module = frame.f_globals.get("__name__", "<unknown>")
        function = frame.f_code.co_name
        call_id = uuid4().hex[:12]
        params: Dict[str, Any] | None = None
        context = CallContext(
            call_id=call_id,
            module=str(module),
            function=function,
            filename=str(frame.f_code.co_filename),
            lineno=frame.f_lineno,
            start_ns=time.perf_counter_ns(),
            parameters=params,
        )
        _CALL_STATE[frame_id] = context
        return

    context = _CALL_STATE.get(frame_id)
    if context is None:
        return

    if event == "return":
        _CALL_STATE.pop(frame_id, None)
        duration_ms = (time.perf_counter_ns() - context.start_ns) / 1_000_000
        if _AUTO_LOG_MIN_DURATION_MS and duration_ms < _AUTO_LOG_MIN_DURATION_MS:
            return
        if context.parameters is None:
            context.parameters = _capture_parameters(frame)
        _AUTO_LOGGER.write(
            {
                "timestamp": _now_iso(),
                "event": "call.success",
                "call_id": context.call_id,
                "module": context.module,
                "function": context.function,
                "filename": context.filename,
                "line": context.lineno,
                "duration_ms": round(duration_ms, 3),
                "parameters": context.parameters,
                "result": _json_safe(arg),
            }
        )
    elif event == "exception":
        _CALL_STATE.pop(frame_id, None)
        exc_type, exc_value, _exc_tb = arg
        duration_ms = (time.perf_counter_ns() - context.start_ns) / 1_000_000
        if context.parameters is None:
            context.parameters = _capture_parameters(frame)
        _AUTO_LOGGER.write(
            {
                "timestamp": _now_iso(),
                "event": "call.exception",
                "call_id": context.call_id,
                "module": context.module,
                "function": context.function,
                "filename": context.filename,
                "line": context.lineno,
                "duration_ms": round(duration_ms, 3),
                "parameters": context.parameters,
                "exception": {
                    "type": getattr(exc_type, "__name__", str(exc_type)),
                    "message": _truncate_string(str(exc_value) if exc_value else ""),
                },
            }
        )


def start_auto_logger() -> None:
    """Install a profiling hook that records every project function call."""

    global _AUTO_LOGGER_ENABLED
    if not _LOGGING_ENABLED:
        return
    if _AUTO_LOGGER_ENABLED:
        return
    if os.getenv("AUTO_LOGGER_DISABLED") in {"1", "true", "TRUE"}:
        return

    previous = getattr(sys, "getprofile", lambda: None)()

    def _wrapper(frame: FrameType, event: str, arg: Any) -> None:
        _profile_dispatch(frame, event, arg)
        if previous:
            previous(frame, event, arg)

    threading.setprofile(_wrapper)
    sys.setprofile(_wrapper)
    global _PROFILE_WRAPPER
    _PROFILE_WRAPPER = _wrapper
    _AUTO_LOGGER_ENABLED = True


# ---- opt-in decorator instrumentation --------------------------------------


def log_call(func):
    """Decorator alternative for selective instrumentation."""

    if getattr(func, "__log_wrapped__", False):
        return func

    def wrapper(*args, **kwargs):
        call_id = uuid4().hex[:12]
        start_ns = time.perf_counter_ns()
        module = func.__module__
        func_name = func.__qualname__
        params = _bind_arguments(func, *args, **kwargs)
        _AUTO_LOGGER.write(
            {
                "timestamp": _now_iso(),
                "event": "call.start",
                "call_id": call_id,
                "module": module,
                "function": func_name,
                "parameters": params,
            }
        )
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
            _AUTO_LOGGER.write(
                {
                    "timestamp": _now_iso(),
                    "event": "call.exception",
                    "call_id": call_id,
                    "module": module,
                    "function": func_name,
                    "duration_ms": round(duration_ms, 3),
                    "exception": {
                        "type": type(exc).__name__,
                        "message": _truncate_string(str(exc)),
                    },
                }
            )
            raise
        duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
        _AUTO_LOGGER.write(
            {
                "timestamp": _now_iso(),
                "event": "call.success",
                "call_id": call_id,
                "module": module,
                "function": func_name,
                "duration_ms": round(duration_ms, 3),
                "result": _json_safe(result),
            }
        )
        return result

    setattr(wrapper, "__log_wrapped__", True)
    return wrapper


def _bind_arguments(func, *args, **kwargs) -> MutableMapping[str, Any]:
    signature = inspect.signature(func)
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        bound = signature.bind_partial()
    bound.apply_defaults()
    data: MutableMapping[str, Any] = {}
    for name, value in bound.arguments.items():
        if name in {"self", "cls"}:
            continue
        data[name] = _json_safe(value)
    return data


def auto_log_module(module: str | Any, *, include_private: bool = False, skip: Iterable[str] | None = None) -> None:
    """Instrument module-level functions and classes via decorators."""

    if isinstance(module, str):
        if module not in sys.modules:
            raise ValueError(f"Module {module!r} not imported; import it before instrumentation.")
        module_obj = sys.modules[module]
    else:
        module_obj = module
        module = getattr(module_obj, "__name__", repr(module_obj))

    if module == __name__:
        return

    skip_set = set(skip or ())
    if not include_private:
        skip_set.update({name for name in dir(module_obj) if name.startswith("_")})

    for attr_name, attr_value in inspect.getmembers(module_obj):
        if attr_name in skip_set:
            continue
        if inspect.isfunction(attr_value) and attr_value.__module__ == module:
            setattr(module_obj, attr_name, log_call(attr_value))
        elif inspect.isclass(attr_value) and attr_value.__module__ == module:
            _instrument_class(attr_value, skip_set)


def _instrument_class(cls: type[Any], skip_set: set[str]) -> None:
    for name, member in list(vars(cls).items()):
        if name in skip_set or name.startswith("_"):
            continue
        if isinstance(member, staticmethod):
            setattr(cls, name, staticmethod(log_call(member.__func__)))
        elif isinstance(member, classmethod):
            setattr(cls, name, classmethod(log_call(member.__func__)))
        elif inspect.isfunction(member):
            setattr(cls, name, log_call(member))
