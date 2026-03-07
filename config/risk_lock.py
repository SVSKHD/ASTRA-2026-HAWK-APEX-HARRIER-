# config/risk_lock.py
from __future__ import annotations

"""
Risk Lock Configuration — Global loss protection + Per-trade profit lock.

Two concepts:
    1. Global Loss Lock  — Account-wide daily loss limit (stops ALL trading)
    2. Min Profit Lock   — If trade was in profit and reverses, close at min profit

Usage:
    from config.risk_lock import RISK_LOCK, MinProfitLock

    # Check global loss
    if RISK_LOCK.should_stop_all(total_realized=-55.0):
        # Stop all trading

    # Check if trade should close to lock profit
    if RISK_LOCK.min_profit.should_close(peak_profit=25.0, current_profit=8.0):
        # Close trade to lock minimum profit
"""

from dataclasses import dataclass
from typing import Tuple, Optional
import os


# ---------------------------------------------------------------------------
# Min Profit Lock — Per-trade profit protection
# ---------------------------------------------------------------------------

@dataclass
class MinProfitLock:
    """
    Locks in minimum profit when trade reverses.

    Logic:
        1. Trade enters, profit fluctuates
        2. Profit hits peak (e.g., $30)
        3. Price reverses, profit drops
        4. When profit drops to min_lock threshold, close trade

    Example:
        trigger_usd = 15.0   # Activate lock after $15 profit
        min_lock_usd = 5.0   # Close if profit drops to $5

        Trade hits $20 profit → lock activated
        Trade drops to $5 profit → CLOSE (locked $5 instead of risking $0)
    """

    # --------------------------
    # Activation
    # --------------------------
    enabled: bool = True
    """Enable/disable min profit lock."""

    trigger_usd: float = 15.0
    """Activate profit lock after reaching this profit. 0 = always active."""

    trigger_pips: float = 0.0
    """Alternative: activate after this many pips profit. 0 = use USD."""

    # --------------------------
    # Lock Levels
    # --------------------------
    min_lock_usd: float = 5.0
    """Close trade if profit drops to this level (after trigger hit)."""

    min_lock_pips: float = 0.0
    """Alternative: close at this pip profit. 0 = use USD."""

    # --------------------------
    # Percentage Mode
    # --------------------------
    use_percentage: bool = False
    """If True, use percentage of peak profit instead of fixed values."""

    lock_pct_of_peak: float = 30.0
    """Lock this % of peak profit. E.g., 30 = if peak was $100, close at $30."""

    def should_close(
            self,
            peak_profit: float,
            current_profit: float,
            peak_pips: float = 0.0,
            current_pips: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Check if trade should close to lock in minimum profit.

        Args:
            peak_profit: Highest profit reached during trade (USD)
            current_profit: Current profit (USD)
            peak_pips: Highest profit in pips (optional)
            current_pips: Current profit in pips (optional)

        Returns:
            (should_close, reason)
        """
        if not self.enabled:
            return False, "disabled"

        # Check if trigger was ever hit
        trigger_hit = False

        if self.trigger_pips > 0 and peak_pips >= self.trigger_pips:
            trigger_hit = True
        elif self.trigger_usd > 0 and peak_profit >= self.trigger_usd:
            trigger_hit = True
        elif self.trigger_usd == 0 and self.trigger_pips == 0:
            # No trigger = always active when in profit
            trigger_hit = peak_profit > 0

        if not trigger_hit:
            return False, "trigger_not_hit"

        # Percentage mode
        if self.use_percentage and peak_profit > 0:
            min_lock = peak_profit * (self.lock_pct_of_peak / 100.0)
            if current_profit <= min_lock:
                return True, f"pct_lock: ${current_profit:.2f} <= {self.lock_pct_of_peak}% of ${peak_profit:.2f}"

        # Pips mode
        if self.min_lock_pips > 0 and current_pips <= self.min_lock_pips:
            return True, f"pip_lock: {current_pips:.1f} pips <= {self.min_lock_pips} pips"

        # USD mode
        if self.min_lock_usd > 0 and current_profit <= self.min_lock_usd:
            return True, f"usd_lock: ${current_profit:.2f} <= ${self.min_lock_usd:.2f}"

        return False, "ok"


# ---------------------------------------------------------------------------
# Global Risk Lock — Account-wide protection
# ---------------------------------------------------------------------------

@dataclass
class GlobalRiskLock:
    """
    Account-wide risk protection.

    Features:
        - Global daily loss limit (stops ALL trading)
        - Catastrophic loss (force close everything)
        - Per-trade min profit lock
    """

    # --------------------------
    # Global Loss Limits
    # --------------------------
    daily_loss_limit_usd: float = -100.0
    """Stop ALL trading after losing this much in a day. Must be negative."""

    catastrophic_loss_usd: float = -200.0
    """Force close ALL positions. Emergency stop. Must be negative."""

    # --------------------------
    # Global Profit Lock (optional)
    # --------------------------
    daily_profit_target_usd: float = 0.0
    """Stop trading after hitting daily profit target. 0 = disabled."""

    # --------------------------
    # Per-Trade Protection
    # --------------------------
    min_profit: MinProfitLock = None
    """Per-trade minimum profit lock configuration."""

    # --------------------------
    # Behavior
    # --------------------------
    reset_at_mt5_midnight: bool = True
    """Reset daily counters at MT5 server midnight."""

    allow_exits_when_locked: bool = True
    """Allow closing positions even when loss-locked (to cut losses)."""

    def __post_init__(self):
        # Ensure loss values are negative
        if self.daily_loss_limit_usd > 0:
            self.daily_loss_limit_usd = -self.daily_loss_limit_usd
        if self.catastrophic_loss_usd > 0:
            self.catastrophic_loss_usd = -self.catastrophic_loss_usd

        # Default min profit lock
        if self.min_profit is None:
            self.min_profit = MinProfitLock()

    def should_stop_all(
            self,
            total_realized: float,
            total_floating: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Check if ALL trading should stop (global lock).

        Args:
            total_realized: Total realized P&L across all symbols today
            total_floating: Total floating P&L from all open positions

        Returns:
            (should_stop, reason)
        """
        total = total_realized + total_floating

        # Daily loss limit
        if self.daily_loss_limit_usd < 0 and total <= self.daily_loss_limit_usd:
            return True, f"daily_loss_limit: ${total:.2f} <= ${self.daily_loss_limit_usd:.2f}"

        # Catastrophic loss
        if self.catastrophic_loss_usd < 0 and total <= self.catastrophic_loss_usd:
            return True, f"catastrophic_loss: ${total:.2f} <= ${self.catastrophic_loss_usd:.2f}"

        # Daily profit target (optional)
        if self.daily_profit_target_usd > 0 and total_realized >= self.daily_profit_target_usd:
            return True, f"daily_profit_target: ${total_realized:.2f} >= ${self.daily_profit_target_usd:.2f}"

        return False, "ok"

    def should_force_close_all(
            self,
            total_realized: float,
            total_floating: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Check if ALL positions should be force-closed (catastrophic only).

        Returns:
            (should_close, reason)
        """
        total = total_realized + total_floating

        if self.catastrophic_loss_usd < 0 and total <= self.catastrophic_loss_usd:
            return True, f"catastrophic_loss: ${total:.2f} <= ${self.catastrophic_loss_usd:.2f}"

        return False, "ok"

    def check_trade_profit_lock(
            self,
            peak_profit: float,
            current_profit: float,
            peak_pips: float = 0.0,
            current_pips: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Check if a specific trade should close to lock profit.
        Delegates to MinProfitLock.

        Returns:
            (should_close, reason)
        """
        return self.min_profit.should_close(
            peak_profit=peak_profit,
            current_profit=current_profit,
            peak_pips=peak_pips,
            current_pips=current_pips,
        )


# ---------------------------------------------------------------------------
# Default Configuration (loaded from env or hardcoded)
# ---------------------------------------------------------------------------

def _get_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).lower() in ("1", "true", "yes")


# Global singleton
RISK_LOCK = GlobalRiskLock(
    # Global limits (from env or defaults)
    daily_loss_limit_usd=_get_float("DAILY_LOSS_LIMIT_USD", -100.0),
    catastrophic_loss_usd=_get_float("CATASTROPHIC_LOSS_USD", -200.0),
    daily_profit_target_usd=_get_float("DAILY_PROFIT_TARGET_USD", 0.0),

    # Per-trade min profit lock
    min_profit=MinProfitLock(
        enabled=_get_bool("MIN_PROFIT_LOCK_ENABLED", True),
        trigger_usd=_get_float("MIN_PROFIT_TRIGGER_USD", 15.0),
        min_lock_usd=_get_float("MIN_PROFIT_LOCK_USD", 5.0),
        use_percentage=_get_bool("MIN_PROFIT_USE_PCT", False),
        lock_pct_of_peak=_get_float("MIN_PROFIT_LOCK_PCT", 30.0),
    ),
)

# ---------------------------------------------------------------------------
# Preset Configurations
# ---------------------------------------------------------------------------

RISK_CONSERVATIVE = GlobalRiskLock(
    daily_loss_limit_usd=-50.0,
    catastrophic_loss_usd=-100.0,
    daily_profit_target_usd=100.0,
    min_profit=MinProfitLock(
        enabled=True,
        trigger_usd=10.0,
        min_lock_usd=3.0,
    ),
)
"""Conservative: -$50 daily loss, lock $3 after $10 profit."""

RISK_AGGRESSIVE = GlobalRiskLock(
    daily_loss_limit_usd=-200.0,
    catastrophic_loss_usd=-500.0,
    daily_profit_target_usd=0.0,  # No profit target
    min_profit=MinProfitLock(
        enabled=True,
        trigger_usd=30.0,
        min_lock_usd=10.0,
    ),
)
"""Aggressive: -$200 daily loss, lock $10 after $30 profit."""

RISK_PERCENTAGE = GlobalRiskLock(
    daily_loss_limit_usd=-100.0,
    catastrophic_loss_usd=-200.0,
    min_profit=MinProfitLock(
        enabled=True,
        use_percentage=True,
        lock_pct_of_peak=50.0,  # Lock 50% of peak profit
    ),
)
"""Percentage mode: lock 50% of peak profit."""

RISK_DISABLED = GlobalRiskLock(
    daily_loss_limit_usd=0.0,
    catastrophic_loss_usd=0.0,
    daily_profit_target_usd=0.0,
    min_profit=MinProfitLock(enabled=False),
)
"""All protections disabled - FOR TESTING ONLY."""