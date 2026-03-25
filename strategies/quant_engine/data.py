"""数据获取层 — 抽象接口 + AKShare/Tushare 实现"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd
from loguru import logger


# ═══════════════════════════════════════════
#  抽象接口（可替换 Tushare / AKShare / Wind）
# ═══════════════════════════════════════════

class BaseDataLoader(ABC):
    """数据加载器抽象基类"""

    @abstractmethod
    def get_index_weights(self, index_code: str, date: str) -> pd.DataFrame:
        """获取指数成分股及权重 → DataFrame[stock_code, weight]"""

    @abstractmethod
    def get_daily_prices(
        self, stock_list: list[str], start: str, end: str
    ) -> pd.DataFrame:
        """获取日线行情 → DataFrame[date, stock_code, open, high, low, close, volume, amount]"""

    @abstractmethod
    def get_financial_data(
        self, stock_list: list[str], report_date: str
    ) -> pd.DataFrame:
        """获取财务数据 → DataFrame[stock_code, roe, eps, revenue, net_profit, total_assets, ...]"""

    @abstractmethod
    def get_index_daily(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        """获取指数日线 → DataFrame[date, close]"""

    @abstractmethod
    def get_industry(self, stock_list: list[str], date: str) -> pd.DataFrame:
        """获取行业分类 → DataFrame[stock_code, industry]（申万一级）"""


# ═══════════════════════════════════════════
#  AKShare 实现（免费，零成本）
# ═══════════════════════════════════════════

class AKShareDataLoader(BaseDataLoader):
    """基于 AKShare 的数据加载器"""

    def __init__(self, cache_dir: str = "./data"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, name: str) -> str:
        return os.path.join(self.cache_dir, f"{name}.parquet")

    def _load_or_fetch(self, name: str, fetch_fn, **kwargs) -> pd.DataFrame:
        path = self._cache_path(name)
        if os.path.exists(path):
            logger.debug(f"缓存命中: {name}")
            return pd.read_parquet(path)
        logger.info(f"正在获取: {name}")
        df = fetch_fn(**kwargs)
        if not df.empty:
            df.to_parquet(path, index=False)
        return df

    # ── 指数成分股 ──
    def get_index_weights(self, index_code: str, date: str) -> pd.DataFrame:
        import akshare as ak

        def fetch():
            # AKShare 中证500成分股
            df = ak.index_stock_cons_weight_csindex(symbol="000905")
            df = df.rename(columns={"成分券代码": "stock_code", "权重": "weight"})
            df["weight"] = df["weight"].astype(float) / 100.0
            # 补全后缀
            df["stock_code"] = df["stock_code"].apply(
                lambda x: f"{x}.SH" if x.startswith(("6", "5")) else f"{x}.SZ"
            )
            return df[["stock_code", "weight"]]

        return self._load_or_fetch(f"index_weights_{index_code}_{date}", fetch)

    # ── 日线行情 ──
    def get_daily_prices(
        self, stock_list: list[str], start: str, end: str
    ) -> pd.DataFrame:
        import akshare as ak

        cache_name = f"daily_{start}_{end}"
        path = self._cache_path(cache_name)
        if os.path.exists(path):
            logger.debug(f"缓存命中: {cache_name}")
            return pd.read_parquet(path)

        all_data = []
        for i, code in enumerate(stock_list):
            symbol = code.split(".")[0]  # 去后缀
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="daily",
                    start_date=start, end_date=end, adjust="hfq"
                )
                if df.empty:
                    continue
                df = df.rename(columns={
                    "日期": "date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close",
                    "成交量": "volume", "成交额": "amount",
                })
                df["stock_code"] = code
                df["date"] = pd.to_datetime(df["date"])
                all_data.append(df[["date", "stock_code", "open", "high",
                                    "low", "close", "volume", "amount"]])
            except Exception as e:
                logger.warning(f"获取 {code} 失败: {e}")

            if (i + 1) % 50 == 0:
                logger.info(f"行情进度: {i+1}/{len(stock_list)}")
                time.sleep(1)  # 限频保护

        result = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
        if not result.empty:
            result.to_parquet(path, index=False)
        return result

    # ── 财务数据 ──
    def get_financial_data(
        self, stock_list: list[str], report_date: str
    ) -> pd.DataFrame:
        import akshare as ak

        def fetch():
            # 使用AKShare获取关键财务指标
            try:
                # 盈利能力
                roe_df = ak.stock_yjbb_em(date=report_date)
                roe_df = roe_df.rename(columns={
                    "股票代码": "symbol",
                    "净资产收益率": "roe",
                    "每股收益": "eps",
                    "营业总收入-同比增长": "revenue_growth",
                    "净利润-同比增长": "profit_growth",
                })
                roe_df["stock_code"] = roe_df["symbol"].apply(
                    lambda x: f"{x}.SH" if str(x).startswith(("6", "5")) else f"{x}.SZ"
                )
                cols = ["stock_code", "roe", "eps", "revenue_growth", "profit_growth"]
                return roe_df[[c for c in cols if c in roe_df.columns]]
            except Exception as e:
                logger.warning(f"财务数据获取失败: {e}")
                return pd.DataFrame()

        return self._load_or_fetch(f"financial_{report_date}", fetch)

    # ── 指数日线 ──
    def get_index_daily(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        def fetch():
            df = ak.index_zh_a_hist(
                symbol="000905", period="daily",
                start_date=start, end_date=end
            )
            df = df.rename(columns={"日期": "date", "收盘": "close"})
            df["date"] = pd.to_datetime(df["date"])
            return df[["date", "close"]]

        return self._load_or_fetch(f"index_{index_code}_{start}_{end}", fetch)

    # ── 行业分类 ──
    def get_industry(self, stock_list: list[str], date: str) -> pd.DataFrame:
        import akshare as ak

        def fetch():
            try:
                df = ak.stock_board_industry_name_em()
                # 简化处理：通过个股所属行业板块获取
                results = []
                for code in stock_list:
                    symbol = code.split(".")[0]
                    try:
                        ind = ak.stock_individual_info_em(symbol=symbol)
                        industry = ind.loc[ind["item"] == "行业", "value"].values
                        results.append({
                            "stock_code": code,
                            "industry": industry[0] if len(industry) > 0 else "未知",
                        })
                    except Exception:
                        results.append({"stock_code": code, "industry": "未知"})
                return pd.DataFrame(results)
            except Exception as e:
                logger.warning(f"行业数据获取失败: {e}")
                return pd.DataFrame(
                    {"stock_code": stock_list, "industry": "未知"}
                )

        return self._load_or_fetch(f"industry_{date}", fetch)


# ═══════════════════════════════════════════
#  Tushare 实现（需Token）
# ═══════════════════════════════════════════

class TushareDataLoader(BaseDataLoader):
    """基于 Tushare Pro 的数据加载器"""

    def __init__(self, token: str, cache_dir: str = "./data"):
        import tushare as ts
        ts.set_token(token)
        self.pro = ts.pro_api()
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, name: str) -> str:
        return os.path.join(self.cache_dir, f"{name}.parquet")

    def _load_or_fetch(self, name: str, fetch_fn) -> pd.DataFrame:
        path = self._cache_path(name)
        if os.path.exists(path):
            return pd.read_parquet(path)
        df = fetch_fn()
        if not df.empty:
            df.to_parquet(path, index=False)
        return df

    def get_index_weights(self, index_code: str, date: str) -> pd.DataFrame:
        def fetch():
            df = self.pro.index_weight(index_code=index_code, start_date=date, end_date=date)
            df = df.rename(columns={"con_code": "stock_code", "weight": "weight"})
            df["weight"] = df["weight"] / 100.0
            return df[["stock_code", "weight"]]
        return self._load_or_fetch(f"ts_weights_{index_code}_{date}", fetch)

    def get_daily_prices(
        self, stock_list: list[str], start: str, end: str
    ) -> pd.DataFrame:
        def fetch():
            all_data = []
            for i, code in enumerate(stock_list):
                try:
                    df = self.pro.daily(ts_code=code, start_date=start, end_date=end, adj="hfq")
                    if not df.empty:
                        df = df.rename(columns={"trade_date": "date", "ts_code": "stock_code"})
                        df["date"] = pd.to_datetime(df["date"])
                        all_data.append(df[["date", "stock_code", "open", "high",
                                            "low", "close", "vol", "amount"]])
                except Exception as e:
                    logger.warning(f"Tushare获取 {code} 失败: {e}")
                if (i + 1) % 100 == 0:
                    time.sleep(0.5)
            return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
        return self._load_or_fetch(f"ts_daily_{start}_{end}", fetch)

    def get_financial_data(self, stock_list: list[str], report_date: str) -> pd.DataFrame:
        def fetch():
            df = self.pro.fina_indicator(period=report_date, fields="ts_code,roe,eps,revenue_yoy,netprofit_yoy")
            df = df.rename(columns={
                "ts_code": "stock_code", "revenue_yoy": "revenue_growth",
                "netprofit_yoy": "profit_growth",
            })
            return df
        return self._load_or_fetch(f"ts_fina_{report_date}", fetch)

    def get_index_daily(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        def fetch():
            df = self.pro.index_daily(ts_code=index_code, start_date=start, end_date=end)
            df = df.rename(columns={"trade_date": "date"})
            df["date"] = pd.to_datetime(df["date"])
            return df[["date", "close"]].sort_values("date")
        return self._load_or_fetch(f"ts_index_{index_code}_{start}_{end}", fetch)

    def get_industry(self, stock_list: list[str], date: str) -> pd.DataFrame:
        def fetch():
            all_data = []
            for code in stock_list:
                try:
                    df = self.pro.stock_basic(ts_code=code, fields="ts_code,industry")
                    all_data.append(df)
                except Exception:
                    pass
            result = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
            result = result.rename(columns={"ts_code": "stock_code"})
            return result
        return self._load_or_fetch(f"ts_industry_{date}", fetch)



# 别名（简短导入）
AKShareLoader = AKShareDataLoader
TushareLoader = TushareDataLoader


def create_loader(source: str = "akshare", **kwargs) -> BaseDataLoader:
    """工厂方法：根据数据源创建DataLoader"""
    if source == "akshare":
        return AKShareDataLoader(cache_dir=kwargs.get("cache_dir", "./data"))
    elif source == "tushare":
        return TushareDataLoader(
            token=kwargs["token"],
            cache_dir=kwargs.get("cache_dir", "./data"),
        )
    else:
        raise ValueError(f"不支持的数据源: {source}")
