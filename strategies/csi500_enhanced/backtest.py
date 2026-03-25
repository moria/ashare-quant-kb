"""回测引擎 — 月频调仓 + 绩效评估 + 可视化"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger


# ═══════════════════════════════════════════
#  绩效评估
# ═══════════════════════════════════════════

@dataclass
class Performance:
    """策略绩效指标"""
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    # 相对基准
    excess_return: float = 0.0
    tracking_error: float = 0.0
    information_ratio: float = 0.0
    # 换手
    avg_turnover: float = 0.0
    total_cost: float = 0.0

    def summary(self) -> str:
        return (
            f"═══ 绩效报告 ═══\n"
            f"累计收益:     {self.total_return:>8.2%}\n"
            f"年化收益:     {self.annual_return:>8.2%}\n"
            f"年化波动:     {self.annual_volatility:>8.2%}\n"
            f"夏普比率:     {self.sharpe_ratio:>8.2f}\n"
            f"最大回撤:     {self.max_drawdown:>8.2%}\n"
            f"Calmar比率:   {self.calmar_ratio:>8.2f}\n"
            f"胜率(月):     {self.win_rate:>8.2%}\n"
            f"───────────────────\n"
            f"超额收益(年): {self.excess_return:>8.2%}\n"
            f"跟踪误差:     {self.tracking_error:>8.2%}\n"
            f"信息比率:     {self.information_ratio:>8.2f}\n"
            f"───────────────────\n"
            f"月均换手:     {self.avg_turnover:>8.2%}\n"
            f"累计成本:     {self.total_cost:>8.2%}\n"
        )


def calc_performance(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    turnover_series: pd.Series | None = None,
    cost_series: pd.Series | None = None,
    rf: float = 0.02,
) -> Performance:
    """计算完整绩效指标"""
    perf = Performance()

    # 基础指标
    cum = (1 + portfolio_returns).cumprod()
    n_years = len(portfolio_returns) / 12  # 月频

    perf.total_return = cum.iloc[-1] - 1 if len(cum) > 0 else 0
    perf.annual_return = (1 + perf.total_return) ** (1 / max(n_years, 0.1)) - 1
    perf.annual_volatility = portfolio_returns.std() * np.sqrt(12)

    if perf.annual_volatility > 0:
        perf.sharpe_ratio = (perf.annual_return - rf) / perf.annual_volatility

    # 最大回撤
    running_max = cum.cummax()
    drawdown = (cum - running_max) / running_max
    perf.max_drawdown = drawdown.min()

    if perf.max_drawdown < 0:
        perf.calmar_ratio = perf.annual_return / abs(perf.max_drawdown)

    # 月度胜率
    perf.win_rate = (portfolio_returns > 0).mean()

    # 相对基准
    excess = portfolio_returns - benchmark_returns
    perf.excess_return = excess.mean() * 12
    perf.tracking_error = excess.std() * np.sqrt(12)
    if perf.tracking_error > 0:
        perf.information_ratio = perf.excess_return / perf.tracking_error

    # 换手
    if turnover_series is not None:
        perf.avg_turnover = turnover_series.mean()
    if cost_series is not None:
        perf.total_cost = cost_series.sum()

    return perf


# ═══════════════════════════════════════════
#  回测引擎
# ═══════════════════════════════════════════

class Backtester:
    """
    月频调仓回测引擎

    流程：每月末 → 获取数据 → 计算因子 → 合成Alpha → 组合优化 → 模拟交易
    """

    def __init__(
        self,
        prices: pd.DataFrame,
        benchmark_prices: pd.Series,
        commission: float = 0.0003,
        stamp_tax: float = 0.0005,
        slippage: float = 0.001,
    ):
        """
        Parameters
        ----------
        prices : 全量日线行情 (date, stock_code, close, ...)
        benchmark_prices : 基准指数日线 (date → close)
        """
        self.prices = prices.copy()
        self.prices["date"] = pd.to_datetime(self.prices["date"])

        # 构建收盘价宽表
        self.close = self.prices.pivot_table(
            index="date", columns="stock_code", values="close"
        ).sort_index()

        # 月度收益率
        self.monthly_close = self.close.resample("ME").last()
        self.monthly_returns = self.monthly_close.pct_change()

        # 基准
        if isinstance(benchmark_prices, pd.DataFrame):
            benchmark_prices = benchmark_prices.set_index("date")["close"]
        benchmark_prices.index = pd.to_datetime(benchmark_prices.index)
        bm_monthly = benchmark_prices.resample("ME").last()
        self.benchmark_returns = bm_monthly.pct_change()

        # 交易成本
        self.commission = commission
        self.stamp_tax = stamp_tax
        self.slippage = slippage

        # 结果存储
        self.portfolio_returns: list[float] = []
        self.bm_returns: list[float] = []
        self.turnover_list: list[float] = []
        self.cost_list: list[float] = []
        self.weight_history: list[pd.Series] = []
        self.dates: list = []

    def calc_transaction_cost(
        self, new_weights: pd.Series, old_weights: pd.Series
    ) -> tuple[float, float]:
        """计算交易成本和换手率"""
        # 对齐
        all_stocks = new_weights.index.union(old_weights.index)
        nw = new_weights.reindex(all_stocks).fillna(0)
        ow = old_weights.reindex(all_stocks).fillna(0)

        diff = nw - ow
        buy_amount = diff.clip(lower=0).sum()
        sell_amount = (-diff.clip(upper=0)).sum()
        turnover = buy_amount + sell_amount  # 双边换手

        # 成本 = 买入(佣金+滑点) + 卖出(佣金+印花税+滑点)
        buy_cost = buy_amount * (self.commission + self.slippage)
        sell_cost = sell_amount * (self.commission + self.stamp_tax + self.slippage)
        total_cost = buy_cost + sell_cost

        return turnover, total_cost

    def run(
        self,
        weight_fn,
        start_month: int = 12,
    ) -> Performance:
        """
        执行回测

        Parameters
        ----------
        weight_fn : 权重生成函数
            签名: weight_fn(date, prices_up_to_date) → pd.Series[stock_code → weight]
        start_month : 从第几个月开始（需要历史数据计算因子）

        Returns
        -------
        Performance对象
        """
        dates = self.monthly_returns.index[start_month:]
        current_weights = pd.Series(dtype=float)

        for i, date in enumerate(dates):
            # 获取当期基准收益
            if date not in self.benchmark_returns.index:
                continue
            bm_ret = self.benchmark_returns.loc[date]
            if pd.isna(bm_ret):
                continue

            # 调用策略生成新权重
            try:
                new_weights = weight_fn(date, self.close.loc[:date])
            except Exception as e:
                logger.warning(f"{date.strftime('%Y-%m')}: 权重生成失败 ({e}), 保持不变")
                new_weights = current_weights

            if new_weights.empty:
                self.portfolio_returns.append(0.0)
                self.bm_returns.append(bm_ret)
                self.dates.append(date)
                continue

            # 交易成本
            turnover, cost = self.calc_transaction_cost(new_weights, current_weights)

            # 组合月度收益 = Σ(w_i × r_i) - 交易成本
            month_ret = self.monthly_returns.loc[date]
            port_ret = (new_weights * month_ret.reindex(new_weights.index).fillna(0)).sum()
            port_ret -= cost

            # 记录
            self.portfolio_returns.append(port_ret)
            self.bm_returns.append(bm_ret)
            self.turnover_list.append(turnover)
            self.cost_list.append(cost)
            self.weight_history.append(new_weights)
            self.dates.append(date)

            current_weights = new_weights

            if (i + 1) % 12 == 0:
                cum_ret = (1 + pd.Series(self.portfolio_returns)).cumprod().iloc[-1] - 1
                cum_bm = (1 + pd.Series(self.bm_returns)).cumprod().iloc[-1] - 1
                logger.info(
                    f"{date.strftime('%Y-%m')}: "
                    f"策略{cum_ret:+.2%} | 基准{cum_bm:+.2%} | "
                    f"超额{cum_ret - cum_bm:+.2%}"
                )

        # 计算绩效
        port_ret_series = pd.Series(self.portfolio_returns, index=self.dates)
        bm_ret_series = pd.Series(self.bm_returns, index=self.dates)
        turnover_series = pd.Series(self.turnover_list, index=self.dates[:len(self.turnover_list)])
        cost_series = pd.Series(self.cost_list, index=self.dates[:len(self.cost_list)])

        perf = calc_performance(
            port_ret_series, bm_ret_series, turnover_series, cost_series
        )

        return perf

    def plot(self, save_path: str | None = None):
        """绘制回测结果图"""
        try:
            import matplotlib.pyplot as plt
            import matplotlib
            matplotlib.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei"]
            matplotlib.rcParams["axes.unicode_minus"] = False
        except ImportError:
            logger.warning("matplotlib不可用，跳过绘图")
            return

        fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

        dates = self.dates
        port_cum = (1 + pd.Series(self.portfolio_returns, index=dates)).cumprod()
        bm_cum = (1 + pd.Series(self.bm_returns, index=dates)).cumprod()
        excess_cum = port_cum / bm_cum

        # 1. 净值曲线
        ax1 = axes[0]
        ax1.plot(dates, port_cum, label="策略", linewidth=1.5)
        ax1.plot(dates, bm_cum, label="中证500", linewidth=1.5, alpha=0.7)
        ax1.set_ylabel("累计净值")
        ax1.legend()
        ax1.set_title("中证500指数增强策略回测")
        ax1.grid(True, alpha=0.3)

        # 2. 超额收益
        ax2 = axes[1]
        ax2.plot(dates, excess_cum - 1, color="green", linewidth=1.5)
        ax2.fill_between(dates, 0, excess_cum - 1, alpha=0.2, color="green")
        ax2.set_ylabel("累计超额收益")
        ax2.axhline(y=0, color="black", linewidth=0.5)
        ax2.grid(True, alpha=0.3)

        # 3. 月度换手率
        if self.turnover_list:
            ax3 = axes[2]
            ax3.bar(dates[:len(self.turnover_list)], self.turnover_list,
                    width=20, alpha=0.6, color="steelblue")
            ax3.set_ylabel("月度换手率")
            ax3.grid(True, alpha=0.3)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            logger.info(f"图表已保存: {save_path}")
        else:
            plt.show()
