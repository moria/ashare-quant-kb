"""ETF轮动 + 风险平价策略 — 低门槛、可快速实盘"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from quant_engine.core import BaseStrategy, BaseUniverse


class ETFRotationStrategy(BaseStrategy):
    """
    ETF动量轮动 + 风险平价

    逻辑:
    1. 从ETF池中选出动量最强的top_k个
    2. 用风险平价分配权重（风险贡献均等）
    3. 若全部ETF动量为负 → 全仓货币基金（避险）

    优势: 免印花税、流动性好、门槛低、T+1可操作
    """

    name = "ETF动量轮动"

    # 默认ETF池：宽基+行业+商品
    DEFAULT_ETFS = {
        "510300": "沪深300ETF",
        "510500": "中证500ETF",
        "510880": "红利ETF",
        "159915": "创业板ETF",
        "512010": "医药ETF",
        "512660": "军工ETF",
        "512800": "银行ETF",
        "515030": "新能源车ETF",
        "518880": "黄金ETF",
        "511010": "国债ETF",
    }

    def __init__(
        self,
        etf_pool: dict[str, str] | None = None,
        top_k: int = 3,
        momentum_window: int = 20,
        vol_window: int = 60,
        use_risk_parity: bool = True,
        cash_etf: str = "511010",  # 国债ETF作为避险
    ):
        super().__init__(
            top_k=top_k,
            momentum_window=momentum_window,
        )
        self.etf_pool = etf_pool or self.DEFAULT_ETFS
        self.top_k = top_k
        self.momentum_window = momentum_window
        self.vol_window = vol_window
        self.use_risk_parity = use_risk_parity
        self.cash_etf = cash_etf

    def rebalance(
        self,
        date: pd.Timestamp,
        prices: pd.DataFrame,
        universe: BaseUniverse,
        context: dict[str, Any],
    ) -> pd.Series:
        # 匹配ETF代码（prices列名可能有后缀）
        etf_codes = list(self.etf_pool.keys())
        available = [c for c in prices.columns if c.split(".")[0] in etf_codes or c in etf_codes]

        if not available:
            logger.warning("未找到任何ETF数据")
            return pd.Series(dtype=float)

        etf_prices = prices[available].tail(max(self.momentum_window, self.vol_window) + 5)

        # 计算动量（近N日收益率）
        momentum = etf_prices.iloc[-1] / etf_prices.iloc[-self.momentum_window] - 1
        momentum = momentum.dropna()

        # 选出动量最强的top_k
        if (momentum > 0).sum() == 0:
            # 全部动量为负 → 避险
            logger.info(f"{date:%Y-%m}: 全部动量为负，避险至{self.cash_etf}")
            cash_codes = [c for c in available if self.cash_etf in c]
            if cash_codes:
                return pd.Series({cash_codes[0]: 1.0})
            return pd.Series(dtype=float)

        # 只选正动量的
        positive = momentum[momentum > 0].nlargest(self.top_k)
        selected = positive.index.tolist()

        if self.use_risk_parity:
            weights = self._risk_parity_weights(etf_prices[selected])
        else:
            # 等权
            weights = pd.Series(1.0 / len(selected), index=selected)

        weights.name = "weight"
        logger.debug(f"{date:%Y-%m}: 选中 {list(zip(selected, weights.round(3)))}")
        return weights

    def _risk_parity_weights(self, prices: pd.DataFrame) -> pd.Series:
        """风险平价：让每个ETF的风险贡献相等"""
        returns = prices.pct_change().dropna()
        cov = returns.tail(self.vol_window).cov() * 252  # 年化

        n = len(cov)
        if n == 0:
            return pd.Series(dtype=float)

        # 简化风险平价：weight_i ∝ 1/σ_i
        vols = np.sqrt(np.diag(cov.values))
        vols = np.where(vols == 0, 1e-6, vols)
        inv_vol = 1.0 / vols
        weights = inv_vol / inv_vol.sum()

        return pd.Series(weights, index=cov.columns)
