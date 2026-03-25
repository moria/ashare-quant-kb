"""因子计算引擎 — 6大类因子：价值/成长/质量/动量/波动/流动性"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


# ═══════════════════════════════════════════
#  因子预处理工具
# ═══════════════════════════════════════════

def winsorize_mad(series: pd.Series, n: float = 5.0) -> pd.Series:
    """MAD去极值（中位数绝对偏差法）"""
    median = series.median()
    mad = (series - median).abs().median()
    upper = median + n * 1.4826 * mad
    lower = median - n * 1.4826 * mad
    return series.clip(lower, upper)


def standardize(series: pd.Series) -> pd.Series:
    """Z-Score标准化"""
    std = series.std()
    if std == 0 or pd.isna(std):
        return series * 0
    return (series - series.mean()) / std


def neutralize(
    factor: pd.Series,
    industry: pd.Series,
    mktcap: pd.Series | None = None,
) -> pd.Series:
    """行业+市值中性化（截面回归残差法）"""
    df = pd.DataFrame({"factor": factor, "industry": industry})
    if mktcap is not None:
        df["ln_mktcap"] = np.log(mktcap)

    # 行业哑变量
    dummies = pd.get_dummies(df["industry"], prefix="ind", dtype=float)
    X = dummies.copy()
    if mktcap is not None:
        X["ln_mktcap"] = df["ln_mktcap"]

    y = df["factor"]
    valid = y.notna() & X.notna().all(axis=1)
    if valid.sum() < 10:
        return factor

    X_valid = X.loc[valid]
    y_valid = y.loc[valid]

    # OLS回归取残差
    try:
        beta = np.linalg.lstsq(X_valid.values, y_valid.values, rcond=None)[0]
        residual = y_valid - X_valid.values @ beta
        result = pd.Series(np.nan, index=factor.index)
        result.loc[valid] = residual
        return result
    except Exception:
        return factor


def preprocess_factor(
    raw: pd.Series,
    industry: pd.Series | None = None,
    mktcap: pd.Series | None = None,
    direction: int = 1,
) -> pd.Series:
    """因子标准预处理流水线: 去极值→标准化→中性化→方向调整"""
    result = winsorize_mad(raw)
    result = standardize(result)
    if industry is not None:
        result = neutralize(result, industry, mktcap)
        result = standardize(result)  # 中性化后再标准化
    return result * direction


# ═══════════════════════════════════════════
#  因子计算引擎
# ═══════════════════════════════════════════

class FactorEngine:
    """
    多因子计算引擎

    输入: 日线行情 + 财务数据
    输出: 截面因子矩阵 DataFrame[stock_code, factor1, factor2, ...]
    """

    def __init__(self, prices: pd.DataFrame, financial: pd.DataFrame):
        """
        Parameters
        ----------
        prices : 日线行情 (date, stock_code, open, high, low, close, volume, amount)
        financial : 财务数据 (stock_code, roe, eps, revenue_growth, profit_growth)
        """
        self.prices = prices.copy()
        self.prices["date"] = pd.to_datetime(self.prices["date"])
        self.prices = self.prices.sort_values(["stock_code", "date"])
        self.financial = financial.copy() if financial is not None else pd.DataFrame()

    def _pivot(self, col: str) -> pd.DataFrame:
        """将长表转为 date × stock_code 的宽表"""
        return self.prices.pivot_table(
            index="date", columns="stock_code", values=col
        )

    # ── 价值因子 ──

    def calc_ep(self) -> pd.Series:
        """盈利收益率 EP = EPS / Price（越高越便宜）"""
        if "eps" not in self.financial.columns:
            return pd.Series(dtype=float)
        latest_price = self.prices.groupby("stock_code")["close"].last()
        ep = self.financial.set_index("stock_code")["eps"] / latest_price
        ep.name = "ep"
        return ep

    def calc_bp(self) -> pd.Series:
        """账面市值比（需要净资产数据，此处用ROE*EP近似）"""
        if "roe" not in self.financial.columns:
            return pd.Series(dtype=float)
        ep = self.calc_ep()
        roe = self.financial.set_index("stock_code")["roe"] / 100.0
        bp = ep / roe.replace(0, np.nan)
        bp.name = "bp"
        return bp

    # ── 成长因子 ──

    def calc_revenue_growth(self) -> pd.Series:
        """营收同比增速"""
        if "revenue_growth" not in self.financial.columns:
            return pd.Series(dtype=float)
        growth = self.financial.set_index("stock_code")["revenue_growth"].astype(float)
        growth.name = "revenue_growth"
        return growth

    def calc_profit_growth(self) -> pd.Series:
        """净利润同比增速"""
        if "profit_growth" not in self.financial.columns:
            return pd.Series(dtype=float)
        growth = self.financial.set_index("stock_code")["profit_growth"].astype(float)
        growth.name = "profit_growth"
        return growth

    # ── 质量因子 ──

    def calc_roe(self) -> pd.Series:
        """ROE — 净资产收益率"""
        if "roe" not in self.financial.columns:
            return pd.Series(dtype=float)
        roe = self.financial.set_index("stock_code")["roe"].astype(float)
        roe.name = "roe"
        return roe

    # ── 动量因子 ──

    def calc_momentum(self, window: int = 252, skip: int = 21) -> pd.Series:
        """12-1月动量: 过去12个月收益率，剔除最近1个月"""
        close = self._pivot("close")
        ret_full = close.pct_change(window)
        ret_skip = close.pct_change(skip)
        momentum = ret_full.iloc[-1] - ret_skip.iloc[-1]
        momentum.name = "momentum_12_1"
        return momentum

    def calc_reversal(self, window: int = 21) -> pd.Series:
        """1月反转: 过去1个月收益率（负向因子）"""
        close = self._pivot("close")
        reversal = close.pct_change(window).iloc[-1]
        reversal.name = "reversal_1m"
        return reversal

    # ── 波动率因子 ──

    def calc_volatility(self, window: int = 60) -> pd.Series:
        """60日年化波动率（负向因子：低波优选）"""
        close = self._pivot("close")
        daily_ret = close.pct_change()
        vol = daily_ret.rolling(window).std().iloc[-1] * np.sqrt(252)
        vol.name = "volatility"
        return vol

    # ── 流动性因子 ──

    def calc_turnover(self, window: int = 20) -> pd.Series:
        """20日平均换手率（负向因子：低换手优选）"""
        if "volume" not in self.prices.columns:
            return pd.Series(dtype=float)
        volume = self._pivot("volume")
        turnover = volume.rolling(window).mean().iloc[-1]
        turnover.name = "turnover"
        return turnover

    def calc_ln_mktcap(self) -> pd.Series:
        """对数市值（中性化用，非选股因子）"""
        close = self._pivot("close")
        # 简化：以最新收盘价 × 成交量代理（实际应用需总股本数据）
        ln_cap = np.log(close.iloc[-1].replace(0, np.nan))
        ln_cap.name = "ln_mktcap"
        return ln_cap

    # ── 全因子计算 ──

    def compute_all(
        self,
        industry: pd.Series | None = None,
    ) -> pd.DataFrame:
        """
        计算全部因子并返回标准化截面矩阵

        Returns
        -------
        DataFrame[stock_code, ep, bp, roe, revenue_growth, momentum_12_1,
                  reversal_1m, volatility, turnover, ln_mktcap]
        """
        logger.info("开始计算全部因子...")

        # 计算原始因子
        raw_factors = {}
        factor_direction = {
            "ep": 1, "bp": 1, "roe": 1, "revenue_growth": 1,
            "profit_growth": 1, "momentum_12_1": 1,
            "reversal_1m": -1, "volatility": -1, "turnover": -1,
        }

        calculators = {
            "ep": self.calc_ep,
            "bp": self.calc_bp,
            "roe": self.calc_roe,
            "revenue_growth": self.calc_revenue_growth,
            "profit_growth": self.calc_profit_growth,
            "momentum_12_1": self.calc_momentum,
            "reversal_1m": self.calc_reversal,
            "volatility": self.calc_volatility,
            "turnover": self.calc_turnover,
            "ln_mktcap": self.calc_ln_mktcap,
        }

        mktcap = self.calc_ln_mktcap()

        for name, calc_fn in calculators.items():
            try:
                raw = calc_fn()
                if raw.empty:
                    logger.warning(f"因子 {name} 为空，跳过")
                    continue
                if name == "ln_mktcap":
                    raw_factors[name] = standardize(winsorize_mad(raw))
                else:
                    direction = factor_direction.get(name, 1)
                    raw_factors[name] = preprocess_factor(
                        raw, industry=industry, mktcap=mktcap if name != "ln_mktcap" else None,
                        direction=direction,
                    )
                logger.debug(f"因子 {name}: 有效值 {raw_factors[name].notna().sum()}")
            except Exception as e:
                logger.warning(f"因子 {name} 计算失败: {e}")

        if not raw_factors:
            return pd.DataFrame()

        result = pd.DataFrame(raw_factors)
        result.index.name = "stock_code"
        logger.info(f"因子计算完成: {len(result)} 只股票, {len(result.columns)} 个因子")
        return result
