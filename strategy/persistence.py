# strategy/persistence.py
from __future__ import annotations

"""
Persistent storage for strategies.

Folder layout per strategy+symbol pair:
    data/strategy_state/
        astra_hawk/
            XAUUSD/
                state.json              ← live state, overwritten every tick
                shutdown_20260306T142301Z.json  ← clean shutdown snapshot
                resume.log              ← append-only startup/resume history
            EURUSD/
                state.json
                ...
        apex_harrier/
            XAUUSD/
                state.json
                ...

On every tick:
    → state.json overwritten atomically (.tmp → replace)

On clean shutdown (SIGINT / SIGTERM):
    → shutdown_<timestamp>.json written (never overwritten)
    → resume.log entry appended: "shutdown at <ts>"

On restart / restore:
    → state.json loaded
    → gap from saved_utc to now computed
    → resume.log entry appended: "resumed at <ts>, gap=Xm Ys, was_in_trade=..."
    → console print shows the gap clearly
"""

import os
import json
import signal
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def state_dir(base_dir: str, strategy: str, symbol: str) -> str:
    return os.path.join(base_dir, "strategy_state", strategy, symbol)

def _state_file(base_dir: str, strategy: str, symbol: str) -> str:
    return os.path.join(state_dir(base_dir, strategy, symbol), "state.json")

def _resume_log(base_dir: str, strategy: str, symbol: str) -> str:
    return os.path.join(state_dir(base_dir, strategy, symbol), "resume.log")

def _shutdown_file(base_dir: str, strategy: str, symbol: str, ts: str) -> str:
    safe = ts.replace(":", "").replace("-", "").replace("+", "").replace(" ", "")
    return os.path.join(state_dir(base_dir, strategy, symbol), f"shutdown_{safe}.json")


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_state(
    base_dir: str,
    strategy: str,
    symbol:   str,
    state:    Dict[str, Any],
    reason:   str = "tick",     # "tick" | "rollover" | "shutdown" | "crash"
) -> None:
    """
    Atomically saves state.json.
    On shutdown also writes a timestamped snapshot that is never overwritten.
    """
    now = _utc_now()
    payload = {
        **state,
        "strategy":    strategy,
        "symbol":      symbol,
        "saved_utc":   now,
        "save_reason": reason,
    }

    d = state_dir(base_dir, strategy, symbol)
    os.makedirs(d, exist_ok=True)

    # always update live state.json
    _atomic_write(_state_file(base_dir, strategy, symbol), payload)

    # on shutdown write a dated snapshot (permanent, never overwritten)
    if reason == "shutdown":
        snap = _shutdown_file(base_dir, strategy, symbol, now)
        _atomic_write(snap, payload)
        _log(base_dir, strategy, symbol, {
            "event":      "shutdown",
            "ts":         now,
            "in_trade":   state.get("in_trade", False),
            "daily_done": state.get("daily_done", False),
            "date_mt5":   state.get("date_mt5"),
        })
        print(
            f"[{strategy}:{symbol}] 💾 shutdown saved → "
            f"shutdown_{now.replace(':', '').replace('-','')}.json"
        )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_state(
    base_dir: str,
    strategy: str,
    symbol:   str,
) -> Optional[Dict[str, Any]]:
    """
    Loads state.json.
    Writes a resume entry to resume.log.
    Prints gap since last save to console.
    Returns None on fresh start or corrupt file.
    """
    path = _state_file(base_dir, strategy, symbol)
    data = _read_json(path)

    if data is None:
        print(f"[{strategy}:{symbol}] 🆕 fresh start — no saved state found")
        _log(base_dir, strategy, symbol, {
            "event":       "fresh_start",
            "resumed_utc": _utc_now(),
        })
        return None

    saved_utc   = data.get("saved_utc", "")
    save_reason = data.get("save_reason", "unknown")
    date_mt5    = data.get("date_mt5", "unknown")
    in_trade    = data.get("in_trade", False)
    daily_done  = data.get("daily_done", False)
    gap         = _gap(saved_utc)
    now         = _utc_now()

    _log(base_dir, strategy, symbol, {
        "event":        "resume",
        "resumed_utc":  now,
        "from_utc":     saved_utc,
        "save_reason":  save_reason,
        "gap":          gap,
        "date_mt5":     date_mt5,
        "in_trade":     in_trade,
        "daily_done":   daily_done,
    })

    # clear console line for visibility
    print(f"")
    print(f"  ┌─ [{strategy}:{symbol}] RESUMED ──────────────────────────")
    print(f"  │  last saved : {saved_utc}  ({save_reason})")
    print(f"  │  resumed at : {now}")
    print(f"  │  gap        : {gap}")
    print(f"  │  date_mt5   : {date_mt5}")
    print(f"  │  in_trade   : {in_trade}")
    print(f"  │  daily_done : {daily_done}")
    print(f"  └─────────────────────────────────────────────────────────")
    print(f"")

    return data


# ---------------------------------------------------------------------------
# Resume log (append-only JSONL)
# ---------------------------------------------------------------------------

def _log(base_dir: str, strategy: str, symbol: str, entry: Dict[str, Any]) -> None:
    path = _resume_log(base_dir, strategy, symbol)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[persistence] ⚠️ resume.log write failed: {e!r}")


def read_resume_log(
    base_dir: str,
    strategy: str,
    symbol:   str,
) -> List[Dict[str, Any]]:
    """Returns all resume.log entries as a list of dicts."""
    path = _resume_log(base_dir, strategy, symbol)
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[persistence] ⚠️ resume.log read failed: {e!r}")
    return entries


# ---------------------------------------------------------------------------
# ShutdownManager — SIGINT / SIGTERM handler
# ---------------------------------------------------------------------------

class ShutdownManager:
    """
    Singleton. Collects save callbacks from every active strategy instance.
    On SIGINT or SIGTERM → calls all callbacks → exits.

    Usage in runner.py:
        ShutdownManager.get().register("astra_hawk:XAUUSD", callback_fn)
    """

    _inst: Optional["ShutdownManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._cbs: Dict[str, Callable[[], None]] = {}
        self._cb_lock = threading.Lock()
        self._hooked  = False

    @classmethod
    def get(cls) -> "ShutdownManager":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
                cls._inst._hook_signals()
            return cls._inst

    def register(self, key: str, fn: Callable[[], None]) -> None:
        """key = "strategy_name:SYMBOL"  e.g. "astra_hawk:XAUUSD" """
        with self._cb_lock:
            self._cbs[key] = fn

    def unregister(self, key: str) -> None:
        with self._cb_lock:
            self._cbs.pop(key, None)

    def trigger(self, reason: str = "signal") -> None:
        print(f"\n[shutdown] 🛑 {reason} — saving all strategy states ...")
        with self._cb_lock:
            cbs = dict(self._cbs)
        for key, fn in cbs.items():
            try:
                fn()
                print(f"[shutdown] ✅ {key}")
            except Exception as e:
                print(f"[shutdown] ⚠️ {key} failed: {e!r}")
        print("[shutdown] 🏁 done.")
        raise SystemExit(0)

    def _hook_signals(self) -> None:
        if self._hooked:
            return
        self._hooked = True
        try:
            signal.signal(signal.SIGINT,  lambda *_: self.trigger("SIGINT"))
            signal.signal(signal.SIGTERM, lambda *_: self.trigger("SIGTERM"))
        except Exception:
            pass   # non-main thread or restricted env — skip


# ---------------------------------------------------------------------------
# PersistenceMixin — plug into any BaseStrategy subclass
# ---------------------------------------------------------------------------

class PersistenceMixin:
    """
    Mixin for BaseStrategy subclasses.

    What it provides:
        self._persist_load()    → call in restore()
        self._persist_save()    → call in persist() and _on_shutdown()
        self._persist_init()    → call at end of init() to hook shutdown

    Requires self.name, self.symbol, self.base_dir (set by BaseStrategy.init).

    Subclass must implement:
        _build_state() -> Dict[str, Any]   — returns the dict to save
        _apply_state(data: Dict)           — restores state from saved dict
    """

    def _persist_init(self) -> None:
        """Register shutdown callback. Call at end of init()."""
        key = f"{self.name}:{self.symbol}"          # type: ignore[attr-defined]
        ShutdownManager.get().register(key, self._on_shutdown)

    def _persist_load(self) -> Optional[Dict[str, Any]]:
        """Load from disk. Returns raw dict or None on fresh start."""
        return load_state(
            self.base_dir,                          # type: ignore[attr-defined]
            self.name,                              # type: ignore[attr-defined]
            self.symbol,                            # type: ignore[attr-defined]
        )

    def _persist_save(self, reason: str = "tick") -> None:
        """Save current state to disk."""
        state = self._build_state()                 # type: ignore[attr-defined]
        save_state(
            self.base_dir,                          # type: ignore[attr-defined]
            self.name,                              # type: ignore[attr-defined]
            self.symbol,                            # type: ignore[attr-defined]
            state,
            reason=reason,
        )

    def _on_shutdown(self) -> None:
        """Called by ShutdownManager on SIGINT/SIGTERM."""
        self._persist_save(reason="shutdown")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gap(saved_utc: str) -> str:
    """Human readable gap e.g. '4m 12s', 'unknown'."""
    try:
        fmt   = "%Y-%m-%dT%H:%M:%SZ"
        saved = datetime.strptime(saved_utc, fmt).replace(tzinfo=timezone.utc)
        diff  = int((datetime.now(timezone.utc) - saved).total_seconds())
        if diff < 0:
            return "0s"
        h, r = divmod(diff, 3600)
        m, s = divmod(r, 60)
        if h:   return f"{h}h {m}m {s}s"
        if m:   return f"{m}m {s}s"
        return f"{s}s"
    except Exception:
        return "unknown"


def _read_json(path: str) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[persistence] ⚠️ read failed {path}: {e!r}")
        return None


def _atomic_write(path: str, payload: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[persistence] ⚠️ write failed {path}: {e!r}")