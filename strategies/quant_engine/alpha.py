"""Alpha模型 — 因子合成与IC_IR自适应加权"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


class AlphaModel:
    """
    多因子Alpha合成模型

    支持两种合成方式:
    1. 固定权重 (equal / manual)
    2. IC_IR自适应加权 (滚动IC均值/IC标准差)
    """

    def __init__(
        self,
        method: str = "ic_ir",
        ic_window: int = 12,
        factor_weights: dict | None = None,
    ):
        """
        Parameters
        ----------
        method : 'equal' | 'manual' | 'ic_ir'
        ic_window : IC_IR计算的滚动窗口（月数）
        factor_weights : 手动权重字典（method='manual'时使用）
        """
        self.method = method
        self.ic_window = ic_window
        self.manual_weights = factor_weights or {}
        self.ic_history: list[dict] = []

    def update_ic(self, factor_values: pd.DataFrame, forward_returns: pd.Series):
        """
        记录本期IC值

        Parameters
        ----------
        factor_values : 截面因子矩阵 (stock_code × factors)
        forward_returns : 下期实际收益 (stock_code → return)
        """
        ic_record = {}
        common = factor_values.index.intersection(forward_returns.index)
        ret = forward_returns.loc[common]

        for col in factor_values.columns:
            fv = factor_values[col].loc[common].dropna()
            valid = fv.index.intersection(ret.dropna().index)
            if len(valid) < 20:
                continue
            # Rank IC (Spearman)
            ic = fv.loc[valid].rank().corr(ret.loc[valid].rank())
            ic_record[col] = ic

        self.ic_history.append(ic_record)
        logger.debug(f"IC更新: {ic_record}")

    def get_weights(self, factor_names: list[str]) -> dict[str, float]:
        """根据选定方法计算因子权重"""
        if self.method == "equal":
            n = len(factor_names)
            return {f: 1.0 / n for f in factor_names}

        elif self.method == "manual":
            total = sum(self.manual_weights.get(f, 0) for f in factor_names)
            if total == 0:
                return {f: 1.0 / len(factor_names) for f in factor_names}
            return {f: self.manual_weights.get(f, 0) / total for f in factor_names}

        elif self.method == "ic_ir":
            return self._ic_ir_weights(factor_names)

        else:
            raise ValueError(f"不支持的合成方法: {self.method}")

    def _ic_ir_weights(self, factor_names: list[str]) -> dict[str, float]:
        """IC_IR加权: weight_i ∝ mean(IC_i) / std(IC_i)"""
        if len(self.ic_history) < 3:
            # IC历史不足，退化为等权
            logger.warning("IC历史不足3期，使用等权")
            return {f: 1.0 / len(factor_names) for f in factor_names}

        # 取最近ic_window期
        recent = self.ic_history[-self.ic_window:]
        ic_df = pd.DataFrame(recent)

        weights = {}
        for f in factor_names:
            if f not in ic_df.columns:
                weights[f] = 0
                continue
            ic_series = ic_df[f].dropna()
            if len(ic_series) < 3:
                weights[f] = 0
                continue
            ic_mean = ic_series.mean()
            ic_std = ic_series.std()
            # IC_IR = IC均值 / IC标准差，取正值
            ic_ir = ic_mean / ic_std if ic_std > 0 else 0
            weights[f] = max(ic_ir, 0)  # 只取正IC_IR的因子

        total = sum(weights.values())
        if total == 0:
            return {f: 1.0 / len(factor_names) for f in factor_names}

        return {f: w / total for f, w in weights.items()}

    def compute_alpha(self, factor_values: pd.DataFrame) -> pd.Series:
        """
        合成综合Alpha得分

        Parameters
        ----------
        factor_values : 标准化后的截面因子矩阵 (stock_code × factors)

        Returns
        -------
        Series[stock_code → alpha_score]
        """
        # 排除非选股因子
        score_factors = [c for c in factor_values.columns if c != "ln_mktcap"]

        weights = self.get_weights(score_factors)
        logger.info(f"因子权重: {weights}")

        alpha = pd.Series(0.0, index=factor_values.index)
        for factor_name, weight in weights.items():
            if factor_name in factor_values.columns and weight > 0:
                fv = factor_values[factor_name].fillna(0)
                alpha += weight * fv

        alpha.name = "alpha_score"
        return alpha


class SimpleAlphaModel(AlphaModel):
    """简化版Alpha模型 — 直接用配置文件中的固定权重"""

    def __init__(self, factor_weights: dict):
        super().__init__(method="manual", factor_weights=factor_weights)
