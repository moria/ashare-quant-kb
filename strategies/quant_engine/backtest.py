"""通用回测引擎 — 接受任意 BaseStrategy 实例"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

from quant_engine.core import BaseStrategy, BaseUniverse


# ═══════════════════════════════════════════
#  绩效指标
# ═══════════════════════════════════════════

@dataclass
class Performance:
    """回测绩效"""
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    excess_return: float = 0.0
    tracking_error: float = 0.0
    information_ratio: float = 0.0
    avg_turnover: float = 0.0
    total_cost: float = 0.0

    def summary(self) -> str:
        lines = [
            "═══ 绩效报告 ═══",
            f"累计收益:     {self.total_return:>8.2%}",
            f"年化收益:     {self.annual_return:>8.2%}",
            f"年化波动:     {self.annual_volatility:>8.2%}",
            f"夏普比率:     {self.sharpe_ratio:>8.2f}",
            f"最大回撤:     {self.max_drawdown:>8.2%}",
            f"Calmar比率:   {self.calmar_ratio:>8.2f}",
            f"胜率(月):     {self.win_rate:>8.2%}",
            "───────────────────",
            f"超额收益(年): {self.excess_return:>8.2%}",
            f"跟踪误差:     {self.tracking_error:>8.2%}",
            f"信息比率:     {self.information_ratio:>8.2f}",
            "───────────────────",
            f"月均换手:     {self.avg_turnover:>8.2%}",
            f"累计成本:     {self.total_cost:>8.2%}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return self.__dict__


def _calc_perf(port_ret, bm_ret, turnover_s, cost_s, rf=0.02) -> Performance:
    p = Performance()
    cum = (1 + port_ret).cumprod()
    n_years = max(len(port_ret) / 12, 0.1)

    p.total_return = cum.iloc[-1] - 1 if len(cum) else 0
    p.annual_return = (1 + p.total_return) ** (1 / n_years) - 1
    p.annual_volatility = port_ret.std() * np.sqrt(12)
    p.sharpe_ratio = (p.annual_return - rf) / p.annual_volatility if p.annual_volatility > 0 else 0

    dd = (cum - cum.cummax()) / cum.cummax()
    p.max_drawdown = dd.min()
    p.calmar_ratio = p.annual_return / abs(p.max_drawdown) if p.max_drawdown < 0 else 0
    p.win_rate = (port_ret > 0).mean()

    excess = port_ret - bm_ret
    p.excess_return = excess.mean() * 12
    p.tracking_error = excess.std() * np.sqrt(12)
    p.information_ratio = p.excess_return / p.tracking_error if p.tracking_error > 0 else 0

    if turnover_s is not None:
        p.avg_turnover = turnover_s.mean()
    if cost_s is not None:
        p.total_cost = cost_s.sum()
    return p


# ═══════════════════════════════════════════
#  通用回测引擎
# ═══════════════════════════════════════════

class Backtester:
    """
    通用月频回测引擎

    接受任意 BaseStrategy 子类，执行:
      每月末 → strategy.rebalance() → 模拟交易 → 记录绩效

    Example
    -------
        bt = Backtester(
            strategy=my_strategy,
            prices=price_df,
            benchmark=benchmark_df,
        )
        perf = bt.run(context={"financial": fin_df, "industry": ind_series})
        print(perf.summary())
        bt.plot("result.png")
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        prices: pd.DataFrame,
        benchmark: pd.DataFrame | pd.Series,
        universe: BaseUniverse | None = None,
        commission: float = 0.0003,
        stamp_tax: float = 0.0005,
        slippage: float = 0.001,
    ):
        self.strategy = strategy
        self.commission = commission
        self.stamp_tax = stamp_tax
        self.slippage = slippage
        self.universe = universe

        # 构建收盘价宽表
        prices = prices.copy()
        prices["date"] = pd.to_datetime(prices["date"])
        self.close = prices.pivot_table(
            index="date", columns="stock_code", values="close"
        ).sort_index()

        self.monthly_close = self.close.resample("ME").last()
        self.monthly_returns = self.monthly_close.pct_change()

        # 基准
        if isinstance(benchmark, pd.DataFrame):
            benchmark = benchmark.set_index("date")["close"] if "date" in benchmark.columns else benchmark.iloc[:, 0]
        benchmark.index = pd.to_datetime(benchmark.index)
        bm_monthly = benchmark.resample("ME").last()
        self.benchmark_returns = bm_monthly.pct_change()

        # 结果
        self._port_rets: list[float] = []
        self._bm_rets: list[float] = []
        self._turnovers: list[float] = []
        self._costs: list[float] = []
        self._weights: list[pd.Series] = []
        self._dates: list = []

    def _transaction_cost(self, new_w: pd.Series, old_w: pd.Series) -> tuple[float, float]:
        all_s = new_w.index.union(old_w.index)
        nw = new_w.reindex(all_s).fillna(0)
        ow = old_w.reindex(all_s).fillna(0)
        diff = nw - ow
        buy = diff.clip(lower=0).sum()
        sell = (-diff.clip(upper=0)).sum()
        turnover = buy + sell
        cost = buy * (self.commission + self.slippage) + sell * (self.commission + self.stamp_tax + self.slippage)
        return turnover, cost

    def run(
        self,
        context: dict | None = None,
        warmup_months: int = 12,
    ) -> Performance:
        """
        执行回测

        Parameters
        ----------
        context : 传递给策略的上下文（财务数据、行业数据等）
        warmup_months : 预热期（跳过前N个月）
        """
        context = context or {}
        dates = self.monthly_returns.index[warmup_months:]
        current_w = pd.Series(dtype=float)

        self.strategy.on_start(context)
        logger.info(f"回测启动: {self.strategy.name} | {dates[0]:%Y-%m} ~ {dates[-1]:%Y-%m}")

        # 关键修正: 月末T计算权重 → 应用T+1月收益率（避免同期数据偏差）
        for i in range(len(dates) - 1):
            rebal_date = dates[i]       # 调仓决策日（月末T）
            return_date = dates[i + 1]  # 收益实现日（月末T+1）

            if return_date not in self.benchmark_returns.index:
                continue
            bm_ret = self.benchmark_returns.loc[return_date]
            if pd.isna(bm_ret):
                continue

            # 策略调仓：基于截至rebal_date的数据
            try:
                new_w = self.strategy.rebalance(
                    date=rebal_date,
                    prices=self.close.loc[:rebal_date],
                    universe=self.universe,
                    context=context,
                )
            except Exception as e:
                logger.warning(f"{rebal_date:%Y-%m}: rebalance失败 ({e})")
                new_w = current_w

            if new_w is None or new_w.empty:
                self._port_rets.append(0.0)
                self._bm_rets.append(bm_ret)
                self._dates.append(return_date)
                continue

            turnover, cost = self._transaction_cost(new_w, current_w)

            # 关键: 用下个月的收益率评估本次调仓
            month_ret = self.monthly_returns.loc[return_date]
            port_ret = (new_w * month_ret.reindex(new_w.index).fillna(0)).sum() - cost

            self._port_rets.append(port_ret)
            self._bm_rets.append(bm_ret)
            self._turnovers.append(turnover)
            self._costs.append(cost)
            self._weights.append(new_w)
            self._dates.append(return_date)
            current_w = new_w

            if (i + 1) % 12 == 0:
                cum = (1 + pd.Series(self._port_rets)).cumprod().iloc[-1] - 1
                cum_bm = (1 + pd.Series(self._bm_rets)).cumprod().iloc[-1] - 1
                logger.info(f"{rebal_date:%Y-%m}: 策略{cum:+.2%} | 基准{cum_bm:+.2%} | 超额{cum - cum_bm:+.2%}")

        self.strategy.on_end(context)

        perf = _calc_perf(
            pd.Series(self._port_rets, index=self._dates),
            pd.Series(self._bm_rets, index=self._dates),
            pd.Series(self._turnovers, index=self._dates[:len(self._turnovers)]),
            pd.Series(self._costs, index=self._dates[:len(self._costs)]),
        )
        return perf

    def plot(self, save_path: str | None = None):
        """净值曲线 + 超额收益 + 换手率"""
        try:
            import matplotlib.pyplot as plt
            import matplotlib
            matplotlib.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
        except ImportError:
            logger.warning("matplotlib不可用")
            return

        dates = self._dates
        port_cum = (1 + pd.Series(self._port_rets, index=dates)).cumprod()
        bm_cum = (1 + pd.Series(self._bm_rets, index=dates)).cumprod()

        fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

        axes[0].plot(dates, port_cum, label=self.strategy.name, lw=1.5)
        axes[0].plot(dates, bm_cum, label="Benchmark", lw=1.5, alpha=0.7)
        axes[0].set_ylabel("累计净值")
        axes[0].legend()
        axes[0].set_title(f"{self.strategy.name} 回测结果")
        axes[0].grid(True, alpha=0.3)

        excess_cum = port_cum / bm_cum - 1
        axes[1].plot(dates, excess_cum, color="green", lw=1.5)
        axes[1].fill_between(dates, 0, excess_cum, alpha=0.2, color="green")
        axes[1].set_ylabel("累计超额")
        axes[1].axhline(0, color="k", lw=0.5)
        axes[1].grid(True, alpha=0.3)

        if self._turnovers:
            axes[2].bar(dates[:len(self._turnovers)], self._turnovers, width=20, alpha=0.6)
            axes[2].set_ylabel("月换手率")
            axes[2].grid(True, alpha=0.3)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            logger.info(f"图表保存: {save_path}")
        else:
            plt.show()
