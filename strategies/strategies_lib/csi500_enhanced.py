"""中证500指数增强策略 — 多因子选股 + 组合优化"""

from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

from quant_engine.core import BaseStrategy, BaseUniverse
from quant_engine.factors import FactorEngine
from quant_engine.alpha import AlphaModel, SimpleAlphaModel
from quant_engine.optimizer import optimize_portfolio, calc_benchmark_industry_weights


class CSI500EnhancedStrategy(BaseStrategy):
    """
    中证500指数增强

    因子体系: EP/BP/ROE/营收增速/动量/反转/波动/换手
    合成方式: 手动权重 或 IC_IR自适应
    优化约束: 行业中性±3%, 单股≤5%, 持仓50只
    """

    name = "CSI500指数增强"

    def __init__(
        self,
        hold_count: int = 50,
        max_weight: float = 0.05,
        max_industry_dev: float = 0.03,
        alpha_method: str = "manual",
        factor_weights: dict | None = None,
    ):
        super().__init__(
            hold_count=hold_count,
            max_weight=max_weight,
            max_industry_dev=max_industry_dev,
        )
        self.hold_count = hold_count
        self.max_weight = max_weight
        self.max_industry_dev = max_industry_dev

        default_weights = {
            "ep": 0.15, "bp": 0.10, "roe": 0.15, "revenue_growth": 0.10,
            "momentum_12_1": 0.10, "reversal_1m": 0.10,
            "volatility": 0.10, "turnover": 0.05,
        }
        weights = factor_weights or default_weights

        if alpha_method == "ic_ir":
            self.alpha_model = AlphaModel(method="ic_ir", ic_window=12)
        else:
            self.alpha_model = SimpleAlphaModel(factor_weights=weights)

    def rebalance(
        self,
        date: pd.Timestamp,
        prices: pd.DataFrame,
        universe: BaseUniverse,
        context: dict[str, Any],
    ) -> pd.Series:
        financial = context.get("financial", pd.DataFrame())
        industry = context.get("industry")

        # 截取最近1年行情
        lookback = prices.tail(252)
        prices_long = lookback.stack().reset_index()
        prices_long.columns = ["date", "stock_code", "close"]

        # 补充volume（如果context中有）
        full_prices = context.get("full_prices")
        if full_prices is not None:
            vol_data = full_prices[full_prices["date"] <= date].tail(252 * 500)
            prices_long = prices_long.merge(
                vol_data[["date", "stock_code", "volume", "amount"]],
                on=["date", "stock_code"], how="left",
            )

        # 因子计算
        engine = FactorEngine(prices_long, financial)
        factor_matrix = engine.compute_all(industry=industry)
        if factor_matrix.empty:
            return pd.Series(dtype=float)

        # Alpha合成
        alpha = self.alpha_model.compute_alpha(factor_matrix)

        # 基准权重
        bm_weights = universe.get_benchmark_weights(date) if universe else pd.Series(dtype=float)

        # 行业权重
        bm_ind_weights = None
        if industry is not None and not bm_weights.empty:
            bm_ind_weights = calc_benchmark_industry_weights(bm_weights, industry)

        # 组合优化
        return optimize_portfolio(
            alpha_scores=alpha,
            benchmark_weights=bm_weights,
            industry=industry,
            benchmark_industry=bm_ind_weights,
            max_weight=self.max_weight,
            max_industry_dev=self.max_industry_dev,
            hold_count=self.hold_count,
        )
