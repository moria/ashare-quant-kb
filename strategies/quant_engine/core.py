"""核心抽象 — 策略基类、股票池、信号"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


# ═══════════════════════════════════════════
#  信号与持仓
# ═══════════════════════════════════════════

class Direction(Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass
class Signal:
    """交易信号"""
    stock_code: str
    direction: Direction
    weight: float = 0.0
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Portfolio:
    """组合快照"""
    date: pd.Timestamp
    weights: pd.Series       # stock_code → weight
    cash: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def positions(self) -> list[str]:
        return self.weights[self.weights > 0].index.tolist()

    @property
    def stock_count(self) -> int:
        return (self.weights > 0).sum()


# ═══════════════════════════════════════════
#  股票池
# ═══════════════════════════════════════════

class BaseUniverse(ABC):
    """股票池抽象基类"""

    @abstractmethod
    def get_stocks(self, date: pd.Timestamp) -> list[str]:
        """返回指定日期的股票池"""

    @abstractmethod
    def get_benchmark_weights(self, date: pd.Timestamp) -> pd.Series:
        """返回基准权重（指增策略用）"""


class IndexUniverse(BaseUniverse):
    """指数成分股股票池"""

    def __init__(self, index_code: str, loader):
        self.index_code = index_code
        self.loader = loader
        self._cache: dict[str, pd.DataFrame] = {}

    def get_stocks(self, date: pd.Timestamp) -> list[str]:
        weights = self._get_weights(date)
        return weights["stock_code"].tolist()

    def get_benchmark_weights(self, date: pd.Timestamp) -> pd.Series:
        weights = self._get_weights(date)
        return weights.set_index("stock_code")["weight"]

    def _get_weights(self, date: pd.Timestamp) -> pd.DataFrame:
        key = date.strftime("%Y%m%d")
        if key not in self._cache:
            self._cache[key] = self.loader.get_index_weights(
                self.index_code, key
            )
        return self._cache[key]


class FullMarketUniverse(BaseUniverse):
    """全市场股票池（过滤ST、停牌等）"""

    def __init__(self, loader):
        self.loader = loader

    def get_stocks(self, date: pd.Timestamp) -> list[str]:
        # 子类可重写过滤逻辑
        raise NotImplementedError("全市场股票池需实现过滤逻辑")

    def get_benchmark_weights(self, date: pd.Timestamp) -> pd.Series:
        stocks = self.get_stocks(date)
        n = len(stocks)
        return pd.Series(1.0 / n, index=stocks)  # 等权


# ═══════════════════════════════════════════
#  策略基类
# ═══════════════════════════════════════════

class BaseStrategy(ABC):
    """
    策略抽象基类 — 所有策略继承此类

    子类需实现:
        name        : 策略名称
        rebalance() : 生成目标权重
    可选重写:
        on_start()  : 策略启动时调用
        on_data()   : 每个交易日调用
    """

    name: str = "BaseStrategy"

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def rebalance(
        self,
        date: pd.Timestamp,
        prices: pd.DataFrame,
        universe: BaseUniverse,
        context: dict[str, Any],
    ) -> pd.Series:
        """
        调仓逻辑 — 返回目标权重

        Parameters
        ----------
        date : 调仓日期
        prices : 截至当日的收盘价宽表 (date × stock_code)
        universe : 股票池
        context : 上下文（可传入财务数据、行业数据等）

        Returns
        -------
        Series[stock_code → target_weight]，权重之和应为1
        """

    def on_start(self, context: dict[str, Any]):
        """策略启动时调用（可选）"""
        pass

    def on_end(self, context: dict[str, Any]):
        """策略结束时调用（可选）"""
        pass

    def __repr__(self):
        return f"{self.name}({self.params})"
