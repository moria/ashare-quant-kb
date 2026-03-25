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
        failed = []
        for i, code in enumerate(stock_list):
            symbol = code.split(".")[0]  # 去后缀
            success = False
            for attempt in range(3):
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol, period="daily",
                        start_date=start, end_date=end, adjust="hfq"
                    )
                    if df.empty:
                        break
                    df = df.rename(columns={
                        "日期": "date", "开盘": "open", "最高": "high",
                        "最低": "low", "收盘": "close",
                        "成交量": "volume", "成交额": "amount",
                    })
                    df["stock_code"] = code
                    df["date"] = pd.to_datetime(df["date"])
                    all_data.append(df[["date", "stock_code", "open", "high",
                                        "low", "close", "volume", "amount"]])
                    success = True
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = 3 * (attempt + 1)
                        logger.debug(f"获取 {code} 失败(第{attempt+1}次), {wait}s后重试: {e}")
                        time.sleep(wait)
                    else:
                        logger.warning(f"获取 {code} 最终失败: {e}")
                        failed.append(code)

            # 每只股票间隔0.3s，每20只额外休息2s
            time.sleep(0.3)
            if (i + 1) % 20 == 0:
                time.sleep(2)
                logger.info(f"行情进度: {i+1}/{len(stock_list)} (成功{len(all_data)}, 失败{len(failed)})")

        if failed:
            logger.warning(f"共 {len(failed)}/{len(stock_list)} 只股票获取失败")

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



# ═══════════════════════════════════════════
#  BaoStock 实现（免费，稳定，无需Token）
# ═══════════════════════════════════════════

class BaoStockDataLoader(BaseDataLoader):
    """基于 BaoStock 的数据加载器 — 最稳定的免费数据源"""

    def __init__(self, cache_dir: str = "./data"):
        import baostock as bs
        self.bs = bs
        self._lg = bs.login()
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def __del__(self):
        try:
            self.bs.logout()
        except Exception:
            pass

    def _cache_path(self, name: str) -> str:
        return os.path.join(self.cache_dir, f"{name}.parquet")

    def _load_or_fetch(self, name: str, fetch_fn) -> pd.DataFrame:
        path = self._cache_path(name)
        if os.path.exists(path):
            logger.debug(f"缓存命中: {name}")
            return pd.read_parquet(path)
        logger.info(f"正在获取: {name}")
        df = fetch_fn()
        if not df.empty:
            df.to_parquet(path, index=False)
        return df

    def _rs_to_df(self, rs) -> pd.DataFrame:
        data = []
        while (rs.error_code == '0') and rs.next():
            data.append(rs.get_row_data())
        return pd.DataFrame(data, columns=rs.fields) if data else pd.DataFrame()

    @staticmethod
    def _to_bs_code(code: str) -> str:
        """000021.SZ → sz.000021"""
        parts = code.split(".")
        if len(parts) == 2:
            return f"{parts[1].lower()}.{parts[0]}"
        return code

    @staticmethod
    def _to_std_code(bs_code: str) -> str:
        """sz.000021 → 000021.SZ"""
        parts = bs_code.split(".")
        if len(parts) == 2:
            return f"{parts[1]}.{parts[0].upper()}"
        return bs_code

    @staticmethod
    def _fmt_date(d: str) -> str:
        """20190101 → 2019-01-01"""
        if "-" not in d and len(d) == 8:
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d

    def get_index_weights(self, index_code: str, date: str) -> pd.DataFrame:
        def fetch():
            fmt_date = self._fmt_date(date)
            rs = self.bs.query_zz500_stocks(date=fmt_date)
            df = self._rs_to_df(rs)
            if df.empty:
                # 尝试最近交易日
                rs = self.bs.query_zz500_stocks()
                df = self._rs_to_df(rs)
            if df.empty:
                return pd.DataFrame()
            df["stock_code"] = df["code"].apply(self._to_std_code)
            df["weight"] = 1.0 / len(df)  # baostock不提供权重，用等权
            return df[["stock_code", "weight"]]
        return self._load_or_fetch(f"bs_weights_{index_code}_{date}", fetch)

    def get_daily_prices(
        self, stock_list: list[str], start: str, end: str
    ) -> pd.DataFrame:
        cache_name = f"bs_daily_{start}_{end}"
        path = self._cache_path(cache_name)
        if os.path.exists(path):
            logger.debug(f"缓存命中: {cache_name}")
            return pd.read_parquet(path)

        all_data = []
        failed = []
        start_fmt = self._fmt_date(start)
        end_fmt = self._fmt_date(end)

        for i, code in enumerate(stock_list):
            bs_code = self._to_bs_code(code)
            try:
                rs = self.bs.query_history_k_data_plus(
                    bs_code,
                    "date,code,open,high,low,close,volume,amount",
                    start_date=start_fmt, end_date=end_fmt,
                    frequency="d", adjustflag="1",  # 后复权
                )
                df = self._rs_to_df(rs)
                if df.empty:
                    continue
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df["stock_code"] = code
                df["date"] = pd.to_datetime(df["date"])
                all_data.append(df[["date", "stock_code", "open", "high",
                                    "low", "close", "volume", "amount"]])
            except Exception as e:
                logger.warning(f"获取 {code} 失败: {e}")
                failed.append(code)

            if (i + 1) % 50 == 0:
                logger.info(f"行情进度: {i+1}/{len(stock_list)} (成功{len(all_data)}, 失败{len(failed)})")

        if failed:
            logger.warning(f"共 {len(failed)}/{len(stock_list)} 只获取失败")

        result = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
        if not result.empty:
            result.to_parquet(path, index=False)
        return result

    def get_financial_data(
        self, stock_list: list[str], report_date: str
    ) -> pd.DataFrame:
        def fetch():
            # baostock 季报：盈利能力
            year = report_date[:4]
            quarter_map = {"0331": 1, "0630": 2, "0930": 3, "1231": 4}
            quarter = quarter_map.get(report_date[4:], 3)

            all_data = []
            for i, code in enumerate(stock_list):
                bs_code = self._to_bs_code(code)
                try:
                    rs = self.bs.query_profit_data(code=bs_code, year=int(year), quarter=quarter)
                    df = self._rs_to_df(rs)
                    if not df.empty:
                        row = df.iloc[-1]
                        all_data.append({
                            "stock_code": code,
                            "roe": pd.to_numeric(row.get("roeAvg", ""), errors="coerce"),
                            "eps": pd.to_numeric(row.get("epsTTM", ""), errors="coerce"),
                        })
                except Exception:
                    pass
                if (i + 1) % 100 == 0:
                    logger.info(f"财务数据进度: {i+1}/{len(stock_list)}")

            return pd.DataFrame(all_data) if all_data else pd.DataFrame()

        return self._load_or_fetch(f"bs_financial_{report_date}", fetch)

    def get_index_daily(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        def fetch():
            start_fmt = self._fmt_date(start)
            end_fmt = self._fmt_date(end)
            rs = self.bs.query_history_k_data_plus(
                "sh.000905",
                "date,close",
                start_date=start_fmt, end_date=end_fmt,
                frequency="d",
            )
            df = self._rs_to_df(rs)
            if df.empty:
                return pd.DataFrame()
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            return df[["date", "close"]]
        return self._load_or_fetch(f"bs_index_{index_code}_{start}_{end}", fetch)

    def get_industry(self, stock_list: list[str], date: str) -> pd.DataFrame:
        def fetch():
            all_data = []
            for code in stock_list:
                bs_code = self._to_bs_code(code)
                try:
                    rs = self.bs.query_stock_industry(code=bs_code)
                    df = self._rs_to_df(rs)
                    if not df.empty:
                        all_data.append({
                            "stock_code": code,
                            "industry": df.iloc[0].get("industry", "未知"),
                        })
                    else:
                        all_data.append({"stock_code": code, "industry": "未知"})
                except Exception:
                    all_data.append({"stock_code": code, "industry": "未知"})
            return pd.DataFrame(all_data)
        return self._load_or_fetch(f"bs_industry_{date}", fetch)


# 别名（简短导入）
AKShareLoader = AKShareDataLoader
TushareLoader = TushareDataLoader
BaoStockLoader = BaoStockDataLoader


def create_loader(source: str = "akshare", **kwargs) -> BaseDataLoader:
    """工厂方法：根据数据源创建DataLoader"""
    if source == "akshare":
        return AKShareDataLoader(cache_dir=kwargs.get("cache_dir", "./data"))
    elif source == "tushare":
        return TushareDataLoader(
            token=kwargs["token"],
            cache_dir=kwargs.get("cache_dir", "./data"),
        )
    elif source == "baostock":
        return BaoStockDataLoader(cache_dir=kwargs.get("cache_dir", "./data"))
    else:
        raise ValueError(f"不支持的数据源: {source}")
