#!/usr/bin/env python3
"""
中证500指数增强策略 — 主程序入口

流程: 数据采集 → 因子计算 → Alpha合成 → 组合优化 → 月频回测 → 绩效评估

用法:
    python main.py                    # 默认AKShare数据源
    python main.py --source tushare   # Tushare数据源（需设置token）
    python main.py --start 20200101 --end 20251231
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
from loguru import logger

from config import Config
from data_loader import create_data_loader
from factors import FactorEngine
from alpha_model import AlphaModel, SimpleAlphaModel
from optimizer import optimize_portfolio, calc_benchmark_industry_weights
from backtest import Backtester


def parse_args():
    parser = argparse.ArgumentParser(description="中证500指数增强策略")
    parser.add_argument("--source", default="akshare", choices=["akshare", "tushare"])
    parser.add_argument("--token", default="", help="Tushare token")
    parser.add_argument("--start", default="20190101")
    parser.add_argument("--end", default="20251231")
    parser.add_argument("--hold", type=int, default=50, help="持仓股票数")
    parser.add_argument("--method", default="manual", choices=["equal", "manual", "ic_ir"])
    parser.add_argument("--output", default="./output", help="输出目录")
    return parser.parse_args()


def main():
    args = parse_args()
    cfg = Config(
        start_date=args.start,
        end_date=args.end,
        hold_count=args.hold,
        tushare_token=args.token,
    )

    logger.info("=" * 60)
    logger.info("中证500指数增强策略")
    logger.info(f"回测区间: {cfg.start_date} ~ {cfg.end_date}")
    logger.info(f"数据源: {args.source} | 持仓数: {cfg.hold_count} | 合成方法: {args.method}")
    logger.info("=" * 60)

    # ── Step 1: 数据获取 ──
    logger.info("[1/5] 获取数据...")
    loader = create_data_loader(
        source=args.source,
        token=args.token,
        cache_dir=cfg.data_dir,
    )

    # 指数成分股
    index_weights = loader.get_index_weights(cfg.benchmark, cfg.end_date)
    stock_list = index_weights["stock_code"].tolist()
    benchmark_w = index_weights.set_index("stock_code")["weight"]
    logger.info(f"成分股数量: {len(stock_list)}")

    # 日线行情
    prices = loader.get_daily_prices(stock_list, cfg.start_date, cfg.end_date)
    logger.info(f"行情数据: {len(prices)} 条记录")

    # 基准指数
    benchmark_prices = loader.get_index_daily(cfg.benchmark, cfg.start_date, cfg.end_date)

    # 财务数据（最近一期）
    financial = loader.get_financial_data(stock_list, "20240930")

    # 行业分类
    industry_df = loader.get_industry(stock_list, cfg.end_date)
    industry = industry_df.set_index("stock_code")["industry"] if not industry_df.empty else None

    if prices.empty:
        logger.error("行情数据为空，无法继续")
        sys.exit(1)

    # ── Step 2: 构建Alpha模型 ──
    logger.info("[2/5] 构建Alpha模型...")
    if args.method == "ic_ir":
        alpha_model = AlphaModel(method="ic_ir", ic_window=12)
    else:
        alpha_model = SimpleAlphaModel(factor_weights=cfg.factor_weights)

    # ── Step 3: 构建回测权重函数 ──
    logger.info("[3/5] 构建回测框架...")

    # 预计算基准行业权重
    bm_industry_weights = None
    if industry is not None:
        bm_industry_weights = calc_benchmark_industry_weights(benchmark_w, industry)

    def weight_fn(date: pd.Timestamp, close_history: pd.DataFrame) -> pd.Series:
        """每月调仓：因子计算 → Alpha合成 → 组合优化"""
        # 截取该日期前的行情
        lookback = close_history.tail(252)  # 最近1年
        prices_slice = lookback.stack().reset_index()
        prices_slice.columns = ["date", "stock_code", "close"]

        # 补充volume（从原始数据）
        vol_data = prices[prices["date"] <= date].tail(252 * len(stock_list))
        prices_merged = prices_slice.merge(
            vol_data[["date", "stock_code", "volume", "amount"]],
            on=["date", "stock_code"], how="left",
        )

        # 因子计算
        engine = FactorEngine(prices_merged, financial)
        factor_matrix = engine.compute_all(industry=industry)

        if factor_matrix.empty:
            return pd.Series(dtype=float)

        # Alpha合成
        alpha_scores = alpha_model.compute_alpha(factor_matrix)

        # 组合优化
        weights = optimize_portfolio(
            alpha_scores=alpha_scores,
            benchmark_weights=benchmark_w,
            industry=industry,
            benchmark_industry=bm_industry_weights,
            max_weight=cfg.max_weight,
            max_industry_dev=cfg.max_industry_dev,
            hold_count=cfg.hold_count,
        )

        return weights

    # ── Step 4: 执行回测 ──
    logger.info("[4/5] 执行回测...")
    bt = Backtester(
        prices=prices,
        benchmark_prices=benchmark_prices,
        commission=cfg.commission,
        stamp_tax=cfg.stamp_tax,
        slippage=cfg.slippage,
    )

    perf = bt.run(weight_fn=weight_fn, start_month=12)

    # ── Step 5: 输出结果 ──
    logger.info("[5/5] 输出结果...")
    print("\n" + perf.summary())

    # 保存图表
    import os
    os.makedirs(args.output, exist_ok=True)
    bt.plot(save_path=os.path.join(args.output, "backtest_result.png"))

    # 保存绩效到CSV
    result_df = pd.DataFrame({
        "date": bt.dates,
        "portfolio_return": bt.portfolio_returns,
        "benchmark_return": bt.bm_returns,
    })
    result_df.to_csv(os.path.join(args.output, "monthly_returns.csv"), index=False)
    logger.info(f"结果已保存到 {args.output}/")

    return perf


if __name__ == "__main__":
    main()
