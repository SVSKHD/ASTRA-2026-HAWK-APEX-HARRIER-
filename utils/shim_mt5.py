# mt5shim.py
try:
    import MetaTrader5 as mt5
except ModuleNotFoundError:
    import metatrader5 as mt5

__all__ = ["mt5"]