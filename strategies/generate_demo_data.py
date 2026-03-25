#!/usr/bin/env python3
"""生成模拟中证500数据，用于策略回测演示（数据源被限流时使用）"""
import os
import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

np.random.seed(42)

N_STOCKS = 100
START = "2022-01-01"
END = "2025-12-31"
TRADING_DAYS = pd.bdate_range(START, END)

print(f"生成 {N_STOCKS} 只模拟股票 × {len(TRADING_DAYS)} 交易日", flush=True)

# 行业分配
INDUSTRIES = ["银行", "医药", "电子", "食品饮料", "机械", "化工", "计算机", "新能源", "有色金属", "建筑"]

# 生成股票代码（仿真）
stock_codes = [f"{600000 + i:06d}.SH" if i < 50 else f"{1 + i:06d}.SZ" for i in range(N_STOCKS)]

# 生成日线数据
all_data = []
for j, code in enumerate(stock_codes):
    # 每只股票的参数
    mu = np.random.uniform(-0.0002, 0.0005)  # 日均收益
    sigma = np.random.uniform(0.015, 0.04)    # 日波动
    init_price = np.random.uniform(5, 100)

    # 生成收益率（带一些因子暴露）
    returns = np.random.normal(mu, sigma, len(TRADING_DAYS))
    # 加入市场因子
    market_factor = np.random.normal(0, 0.01, len(TRADING_DAYS))
    returns += market_factor * np.random.uniform(0.5, 1.5)

    prices = init_price * np.cumprod(1 + returns)
    volume = np.random.lognormal(15, 1.5, len(TRADING_DAYS)).astype(int)
    amount = prices * volume

    df = pd.DataFrame({
        "date": TRADING_DAYS,
        "stock_code": code,
        "open": prices * np.random.uniform(0.99, 1.01, len(TRADING_DAYS)),
        "high": prices * np.random.uniform(1.0, 1.03, len(TRADING_DAYS)),
        "low": prices * np.random.uniform(0.97, 1.0, len(TRADING_DAYS)),
        "close": prices,
        "volume": volume,
        "amount": amount,
    })
    all_data.append(df)

prices_df = pd.concat(all_data, ignore_index=True)
prices_df.to_parquet(os.path.join(DATA_DIR, "demo_daily_20220101_20251231.parquet"), index=False)
print(f"日线数据: {len(prices_df)} 行", flush=True)

# 基准指数（等权组合 + 漂移）
bench_returns = np.random.normal(0.0001, 0.012, len(TRADING_DAYS))
bench_prices = 5000 * np.cumprod(1 + bench_returns)
bench_df = pd.DataFrame({"date": TRADING_DAYS, "close": bench_prices})
bench_df.to_parquet(os.path.join(DATA_DIR, "demo_index_000905.SH_20220101_20251231.parquet"), index=False)
print(f"基准指数: {len(bench_df)} 行", flush=True)

# 成分股权重
weights_df = pd.DataFrame({"stock_code": stock_codes, "weight": 1.0 / N_STOCKS})
weights_df.to_parquet(os.path.join(DATA_DIR, "demo_weights_000905.SH_20251231.parquet"), index=False)

# 财务数据
fin_df = pd.DataFrame({
    "stock_code": stock_codes,
    "roe": np.random.uniform(0.02, 0.25, N_STOCKS),
    "eps": np.random.uniform(0.1, 5.0, N_STOCKS),
    "revenue_growth": np.random.uniform(-0.1, 0.5, N_STOCKS),
    "profit_growth": np.random.uniform(-0.2, 0.6, N_STOCKS),
})
fin_df.to_parquet(os.path.join(DATA_DIR, "demo_financial_20240930.parquet"), index=False)
print(f"财务数据: {len(fin_df)} 只", flush=True)

# 行业分类
ind_df = pd.DataFrame({
    "stock_code": stock_codes,
    "industry": [INDUSTRIES[i % len(INDUSTRIES)] for i in range(N_STOCKS)],
})
ind_df.to_parquet(os.path.join(DATA_DIR, "demo_industry_20251231.parquet"), index=False)
print(f"行业分类: {len(ind_df)} 只", flush=True)

# ─── ETF 模拟数据 ───
ETF_START = "2019-01-01"
ETF_END = "2025-12-31"
ETF_DAYS = pd.bdate_range(ETF_START, ETF_END)

ETF_POOL = {
    "510300": ("沪深300", 0.0002, 0.012),
    "510500": ("中证500", 0.0003, 0.015),
    "510880": ("红利",    0.0004, 0.010),
    "159915": ("创业板",  0.0001, 0.020),
    "512010": ("医药",    0.0002, 0.018),
    "512660": ("军工",    0.0001, 0.022),
    "512800": ("银行",    0.0003, 0.008),
    "515030": ("新能源车", 0.0002, 0.025),
    "518880": ("黄金",    0.0003, 0.012),
    "511010": ("国债",    0.0001, 0.003),
}

etf_data = []
for code, (name, mu, sigma) in ETF_POOL.items():
    returns = np.random.normal(mu, sigma, len(ETF_DAYS))
    # 加入风格轮动：不同ETF在不同时期表现不同
    cycle = np.sin(np.arange(len(ETF_DAYS)) / 120 * np.pi + hash(code) % 10)
    returns += cycle * 0.002
    init = np.random.uniform(0.8, 4.0)
    prices = init * np.cumprod(1 + returns)
    volume = np.random.lognormal(18, 1, len(ETF_DAYS)).astype(int)

    df = pd.DataFrame({
        "date": ETF_DAYS,
        "stock_code": code,
        "close": prices,
        "volume": volume,
        "amount": prices * volume,
    })
    etf_data.append(df)

etf_df = pd.concat(etf_data, ignore_index=True)
etf_df.to_parquet(os.path.join(DATA_DIR, "demo_etf_daily_20190101_20251231.parquet"), index=False)
print(f"ETF数据: {len(ETF_POOL)}只 × {len(ETF_DAYS)}日 = {len(etf_df)} 行", flush=True)

print("\n模拟数据生成完成！")
print("  CSI500: python3 run.py csi500 --pool_size 100 --hold 30")
print("  ETF:    python3 run.py etf --start 20190101 --end 20251231", flush=True)
