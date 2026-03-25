#!/usr/bin/env python3
"""
A股量化交易框架 — 统一入口

用法:
    python run.py csi500                    # 中证500指增策略
    python run.py etf                       # ETF动量轮动策略
    python run.py csi500 --source tushare --token YOUR_TOKEN
    python run.py etf --start 20200101 --end 20251231
    python run.py list                      # 列出所有可用策略
"""

from __future__ import annotations

import argparse
import os
import sys

# 将当前目录加入path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loguru import logger


STRATEGIES = {
    "csi500": "CSI500指数增强（多因子选股+组合优化）",
    "etf": "ETF动量轮动（风险平价配权）",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="A股量化交易框架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run.py list                        列出可用策略
  python run.py csi500                      运行中证500指增
  python run.py etf --top_k 5               ETF轮动选5只
  python run.py csi500 --source tushare     使用Tushare数据
        """,
    )
    parser.add_argument("strategy", nargs="?", default="list",
                        help="策略名称: csi500 | etf | list")
    parser.add_argument("--source", default="akshare", choices=["akshare", "tushare"])
    parser.add_argument("--token", default="", help="Tushare token")
    parser.add_argument("--start", default="20190101", help="回测起始日")
    parser.add_argument("--end", default="20251231", help="回测结束日")
    parser.add_argument("--output", default="./output", help="输出目录")

    # 策略专用参数
    parser.add_argument("--hold", type=int, default=50, help="[csi500] 持仓股数")
    parser.add_argument("--method", default="manual", choices=["equal", "manual", "ic_ir"],
                        help="[csi500] Alpha合成方法")
    parser.add_argument("--top_k", type=int, default=3, help="[etf] 选择ETF数量")
    parser.add_argument("--momentum", type=int, default=20, help="[etf] 动量窗口(天)")

    return parser.parse_args()


def run_csi500(args):
    """运行中证500指增策略"""
    from quant_engine import Backtester, create_loader
    from quant_engine.core import IndexUniverse
    from strategies_lib.csi500_enhanced import CSI500EnhancedStrategy

    logger.info("=" * 60)
    logger.info("策略: 中证500指数增强")
    logger.info(f"区间: {args.start} ~ {args.end} | 数据源: {args.source}")
    logger.info("=" * 60)

    loader = create_loader(source=args.source, token=args.token, cache_dir="./data")

    # 数据获取
    index_weights = loader.get_index_weights("000905.SH", args.end)
    stock_list = index_weights["stock_code"].tolist()
    logger.info(f"成分股: {len(stock_list)}")

    prices = loader.get_daily_prices(stock_list, args.start, args.end)
    benchmark = loader.get_index_daily("000905.SH", args.start, args.end)
    financial = loader.get_financial_data(stock_list, "20240930")
    industry_df = loader.get_industry(stock_list, args.end)
    industry = industry_df.set_index("stock_code")["industry"] if not industry_df.empty else None

    if prices.empty:
        logger.error("行情数据为空")
        return

    # 构建策略
    strategy = CSI500EnhancedStrategy(
        hold_count=args.hold,
        alpha_method=args.method,
    )
    universe = IndexUniverse("000905.SH", loader)

    # 回测
    bt = Backtester(
        strategy=strategy,
        prices=prices,
        benchmark=benchmark,
        universe=universe,
    )
    context = {
        "financial": financial,
        "industry": industry,
        "full_prices": prices,
    }
    perf = bt.run(context=context)

    print("\n" + perf.summary())
    os.makedirs(args.output, exist_ok=True)
    bt.plot(save_path=os.path.join(args.output, "csi500_backtest.png"))


def run_etf(args):
    """运行ETF轮动策略"""
    from quant_engine import Backtester, create_loader
    from strategies_lib.etf_rotation import ETFRotationStrategy

    logger.info("=" * 60)
    logger.info("策略: ETF动量轮动")
    logger.info(f"区间: {args.start} ~ {args.end} | Top-K: {args.top_k}")
    logger.info("=" * 60)

    strategy = ETFRotationStrategy(
        top_k=args.top_k,
        momentum_window=args.momentum,
    )

    loader = create_loader(source=args.source, token=args.token, cache_dir="./data")

    # 获取ETF行情
    import akshare as ak
    import pandas as pd

    etf_codes = list(strategy.etf_pool.keys())
    all_data = []
    for code in etf_codes:
        try:
            df = ak.fund_etf_hist_em(
                symbol=code, period="daily",
                start_date=args.start, end_date=args.end, adjust="qfq"
            )
            if df.empty:
                continue
            df = df.rename(columns={"日期": "date", "收盘": "close", "成交量": "volume", "成交额": "amount"})
            df["stock_code"] = code
            df["date"] = pd.to_datetime(df["date"])
            all_data.append(df[["date", "stock_code", "close", "volume", "amount"]])
        except Exception as e:
            logger.warning(f"ETF {code} 获取失败: {e}")

    if not all_data:
        logger.error("无ETF数据")
        return

    prices = pd.concat(all_data, ignore_index=True)

    # 基准：沪深300ETF
    benchmark = prices[prices["stock_code"] == "510300"][["date", "close"]]

    bt = Backtester(
        strategy=strategy,
        prices=prices,
        benchmark=benchmark,
    )
    perf = bt.run(warmup_months=3)

    print("\n" + perf.summary())
    os.makedirs(args.output, exist_ok=True)
    bt.plot(save_path=os.path.join(args.output, "etf_rotation_backtest.png"))


def main():
    args = parse_args()

    if args.strategy == "list":
        print("\n可用策略:")
        print("-" * 50)
        for name, desc in STRATEGIES.items():
            print(f"  {name:<12} {desc}")
        print(f"\n用法: python run.py <策略名> [参数]")
        return

    if args.strategy == "csi500":
        run_csi500(args)
    elif args.strategy == "etf":
        run_etf(args)
    else:
        print(f"未知策略: {args.strategy}")
        print(f"可用: {', '.join(STRATEGIES.keys())}")
        sys.exit(1)


if __name__ == "__main__":
    main()
