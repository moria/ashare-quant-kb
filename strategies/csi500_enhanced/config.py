"""中证500指数增强策略 — 全局配置"""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Config:
    # ── 回测区间 ──
    start_date: str = "20190101"
    end_date: str = "20251231"
    benchmark: str = "000905.SH"  # 中证500

    # ── 调仓 ──
    rebalance_freq: str = "M"  # M=月末, W=周五
    hold_count: int = 50       # 持仓股票数

    # ── 因子权重（IC_IR加权初始值，可被自适应覆盖）──
    factor_weights: dict = field(default_factory=lambda: {
        "ep": 0.15,            # 盈利收益率 (1/PE)
        "bp": 0.10,            # 账面市值比
        "roe": 0.15,           # ROE
        "revenue_growth": 0.10,  # 营收增速
        "accrual": 0.10,       # 应计因子（负向）
        "momentum_12_1": 0.10, # 12-1月动量
        "reversal_1m": 0.10,   # 1月反转
        "volatility": 0.10,    # 波动率（负向）
        "turnover": 0.05,      # 换手率（负向）
        "ln_mktcap": 0.05,     # 对数市值（中性化用）
    })

    # ── 组合优化 ──
    max_weight: float = 0.05        # 单股最大权重5%
    max_industry_dev: float = 0.03  # 行业偏离±3%
    max_tracking_error: float = 0.08  # 年化跟踪误差上限8%
    turnover_penalty: float = 0.001   # 换手惩罚系数

    # ── 交易成本 ──
    commission: float = 0.0003   # 佣金万3
    stamp_tax: float = 0.0005    # 印花税万5（卖出）
    slippage: float = 0.001      # 滑点0.1%

    # ── 数据 ──
    data_dir: str = "./data"
    tushare_token: str = ""  # 填入你的token
