# integration-tests/run_all.py
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Iterable, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = PROJECT_ROOT / "integration-tests"


@dataclass
class TestResult:
    path: Path
    returncode: int
    duration_s: float
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def discover_tests(pattern_substring: str | None = None, self_name: str | None = None) -> List[Path]:
    tests: List[Path] = []
    for p in sorted(TEST_DIR.glob("test_*.py")):
        if p.name == (self_name or ""):
            continue
        if pattern_substring and pattern_substring not in p.name:
            continue
        tests.append(p)
    return tests


async def run_one(test_path: Path, timeout_s: float | None) -> TestResult:
    start = monotonic()
    env = os.environ.copy()
    env.setdefault("OPENROUTER_DISABLE_RETRY", "1")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(test_path),
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    communicate_task = asyncio.create_task(proc.communicate())
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            communicate_task,
            timeout=None if timeout_s is None or timeout_s <= 0 else timeout_s,
        )
        rc = proc.returncode or 0
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        communicate_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await communicate_task
        stdout_b = b""
        stderr_b = f"[TIMEOUT] Exceeded {timeout_s:.0f}s limit".encode("utf-8") if timeout_s else b"[TIMEOUT]"
        rc = 124
        await proc.wait()
    finally:
        if not communicate_task.done():
            communicate_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await communicate_task
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is None:
                continue
            with contextlib.suppress(Exception):
                stream.close()
    duration = monotonic() - start
    return TestResult(
        path=test_path,
        returncode=rc,
        duration_s=duration,
        stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
        stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
    )


async def run_all(tests: Iterable[Path], jobs: int, timeout_s: float | None, verbose: bool) -> Tuple[List[TestResult], float]:
    start = monotonic()
    sem = asyncio.Semaphore(max(1, jobs))
    results: List[TestResult] = []

    # ANSI colors (simple, readable)
    RESET = "\x1b[0m"
    GREY = "\x1b[90m"
    GREEN = "\x1b[32m"
    RED = "\x1b[31m"
    YELLOW = "\x1b[33m"

    async def _run_guarded(p: Path) -> None:
        async with sem:
            print(f"{YELLOW}[ RUN ]{RESET} {p.name}")
            res = await run_one(p, timeout_s)
            status = "OK" if res.ok else "FAIL"
            color = GREEN if res.ok else RED
            print(f"{color}[ {status} ]{RESET} {p.name} in {res.duration_s:.2f}s")
            # Always show internal outputs in grey for quick debugging
            out = res.stdout.strip()
            err = res.stderr.strip()
            if out or err:
                if out:
                    print(f"{GREY}{out.rstrip()}{RESET}")
                if err:
                    print(f"{GREY}{err.rstrip()}{RESET}")
            print("")
            results.append(res)

    await asyncio.gather(*(_run_guarded(p) for p in tests))
    total = monotonic() - start
    return results, total


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all integration tests in integration-tests directory.")
    parser.add_argument("-k", metavar="SUBSTR", help="Only run tests with SUBSTR in filename", default=None)
    parser.add_argument("-j", "--jobs", type=int, default=10, help="Number of parallel jobs (default: 10)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Per-test timeout in seconds (default: 180; pass 0 to disable)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output (print test stdout/stderr)")
    return parser.parse_args(argv)


async def main_async(argv: List[str]) -> int:
    args = parse_args(argv)
    self_name = Path(__file__).name
    tests = discover_tests(args.k, self_name=self_name)

    if not tests:
        print("No tests found.")
        return 0

    print(f"Discovered {len(tests)} test(s) in {TEST_DIR.relative_to(PROJECT_ROOT)}")
    results, total_s = await run_all(tests, jobs=max(1, int(args.jobs)), timeout_s=float(args.timeout or 0.0), verbose=bool(args.verbose))

    ok = sum(1 for r in results if r.ok)
    fail = len(results) - ok
    print("")
    print(f"Summary: {ok} passed, {fail} failed in {total_s:.2f}s")
    if fail:
        print("Failures:")
        for r in results:
            if r.ok:
                continue
            print(f"- {r.path.name} (rc={r.returncode}, {r.duration_s:.2f}s)")
    return 0 if fail == 0 else 1


def main() -> int:
    try:
        return asyncio.run(main_async(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
