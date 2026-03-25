"""
quant_engine — A股量化交易通用框架

架构:
    DataLoader  →  FactorEngine  →  AlphaModel  →  Optimizer  →  Backtester
       ↑               ↑               ↑              ↑             ↑
    (可插拔)        (可插拔)        (可插拔)       (可插拔)      (通用)

用法:
    from quant_engine import Backtester, BaseStrategy
    class MyStrategy(BaseStrategy):
        ...
    bt = Backtester(strategy=MyStrategy(), ...)
    perf = bt.run()
"""

from quant_engine.core import BaseStrategy, BaseUniverse, Signal
from quant_engine.data import BaseDataLoader, AKShareLoader, TushareLoader, create_loader
from quant_engine.factors import FactorEngine, preprocess_factor
from quant_engine.alpha import AlphaModel
from quant_engine.optimizer import optimize_portfolio
from quant_engine.backtest import Backtester, Performance

__version__ = "0.1.0"
__all__ = [
    "BaseStrategy", "BaseUniverse", "Signal",
    "BaseDataLoader", "AKShareLoader", "TushareLoader", "create_loader",
    "FactorEngine", "preprocess_factor",
    "AlphaModel",
    "optimize_portfolio",
    "Backtester", "Performance",
]
