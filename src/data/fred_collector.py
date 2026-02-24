"""
FRED collector — macroeconomic indicators from the Federal Reserve.

The FRED API (Federal Reserve Bank of St. Louis) provides free access to
hundreds of macroeconomic time series. This collector fetches the most recent
values for series directly relevant to Polymarket prediction markets.

Series used:
  FEDFUNDS  — Effective Federal Funds Rate (%)
  CPIAUCSL  — Consumer Price Index (index level)
  UNRATE    — Unemployment Rate (%)
  T10Y2Y    — 10-Year minus 2-Year Treasury spread (recession indicator)
  VIXCLS    — CBOE VIX Volatility Index (market fear gauge)

Example output for a Fed rate market:
  "FRED Data — Federal Funds Rate: 4.33% as of 2026-01-01
   (changed -0.25 % from previous reading). This is the benchmark
   interest rate set by the Federal Reserve."

API: https://fred.stlouisfed.org/docs/api/fred/ — free, requires API key.
Get a free key at: https://fredaccount.stlouisfed.org/apikey
Results cached for 6 hours (FRED data updates daily to monthly).
"""

import time

import httpx

from config.settings import FRED_API_KEY
from src.utils.logger import setup_logger

logger = setup_logger("fred_collector")

_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Maps FRED series IDs to market question keywords that trigger fetching them
_SERIES_KEYWORDS: dict[str, list[str]] = {
    "FEDFUNDS": ["fed ", "rate", "interest", "fomc", "federal reserve", "monetary"],
    "CPIAUCSL": ["cpi", "inflation", "consumer price", "price level"],
    "UNRATE":   ["unemployment", "jobs", "employment", "labor", "payroll", "nonfarm"],
    "T10Y2Y":   ["yield", "recession", "treasury", "bond", "economy", "gdp", "invert"],
    "VIXCLS":   ["vix", "volatility", "bitcoin", "btc", "crypto", "stock market", "crash"],
}

# Human-readable labels for each series
_SERIES_META: dict[str, dict] = {
    "FEDFUNDS": {"name": "Federal Funds Rate",              "unit": "%"},
    "CPIAUCSL": {"name": "Consumer Price Index (CPI)",      "unit": "index"},
    "UNRATE":   {"name": "Unemployment Rate",               "unit": "%"},
    "T10Y2Y":   {"name": "10Y–2Y Treasury Yield Spread",    "unit": "pp"},
    "VIXCLS":   {"name": "CBOE VIX Volatility Index",       "unit": "pts"},
}


class FREDCollector:
    """Fetches macroeconomic indicators from FRED for macro-relevant markets."""

    def __init__(self):
        self._cache: dict = {}    # {series_id: (timestamp, article_or_none)}
        self._cache_ttl = 21600   # 6 hours

    def get_macro_data_for_markets(self, markets: list) -> dict:
        """Return FRED macro articles for markets matching economic keywords.

        Args:
            markets: List of market dicts with 'question' key.

        Returns:
            Dict mapping market_id → [article_dict]. Multiple articles
            possible per market if multiple series match.
        """
        if not FRED_API_KEY:
            return {}

        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "").lower()

            relevant_series = [
                series_id
                for series_id, keywords in _SERIES_KEYWORDS.items()
                if any(kw in question for kw in keywords)
            ]

            if not relevant_series:
                continue

            articles = []
            for series_id in relevant_series:
                article = self._get_series_article(series_id)
                if article:
                    articles.append(article)

            if articles:
                results[market_id] = articles

        return results

    def _get_series_article(self, series_id: str) -> dict | None:
        """Fetch the latest observations for a FRED series, using cache."""
        now = time.time()

        cached_time, cached_article = self._cache.get(series_id, (0, None))
        if now - cached_time < self._cache_ttl:
            return cached_article

        try:
            params = {
                "series_id": series_id,
                "api_key": FRED_API_KEY,
                "limit": 3,
                "sort_order": "desc",
                "file_type": "json",
            }

            with httpx.Client(timeout=15) as client:
                response = client.get(_BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()

            observations = data.get("observations", [])
            valid = [o for o in observations if o.get("value", ".") != "."]
            if not valid:
                self._cache[series_id] = (now, None)
                return None

            latest = valid[0]
            value = float(latest["value"])
            date = latest["date"]
            meta = _SERIES_META.get(series_id, {"name": series_id, "unit": ""})

            change_str = ""
            if len(valid) >= 2:
                prev = float(valid[1]["value"])
                change = value - prev
                change_str = f" (changed {change:+.2f} {meta['unit']} from prior reading)"

            summary = (
                f"FRED Data — {meta['name']}: {value:.2f} {meta['unit']} "
                f"as of {date}{change_str}."
            )

            if series_id == "FEDFUNDS":
                summary += " This is the benchmark overnight lending rate set by the Federal Reserve."
            elif series_id == "T10Y2Y":
                if value < 0:
                    summary += (
                        " The yield curve is INVERTED (negative spread) — "
                        "historically a reliable recession predictor."
                    )
                else:
                    summary += " The yield curve is normal (positive spread), consistent with expansion."
            elif series_id == "VIXCLS":
                level = (
                    "low (calm markets)" if value < 15 else
                    "moderate" if value < 25 else
                    "elevated (investor anxiety)" if value < 35 else
                    "extreme (panic)"
                )
                summary += f" VIX at {value:.1f} indicates {level}."

            article = {
                "title": f"FRED: {meta['name']} = {value:.2f} {meta['unit']} ({date})",
                "source": "FRED (Federal Reserve)",
                "description": summary,
                "content": summary,
                "url": f"https://fred.stlouisfed.org/series/{series_id}",
                "published_at": "",
            }

            logger.info("FRED: %s = %.2f %s (%s)", series_id, value, meta["unit"], date)
            self._cache[series_id] = (now, article)
            return article

        except httpx.HTTPStatusError as e:
            logger.warning("FRED: HTTP %d for %s", e.response.status_code, series_id)
        except httpx.RequestError as e:
            logger.warning("FRED: connection error for %s: %s", series_id, type(e).__name__)
        except (ValueError, KeyError, IndexError) as e:
            logger.warning("FRED: parse error for %s: %s", series_id, e)
        except Exception as e:
            logger.warning("FRED: unexpected error for %s: %s", series_id, type(e).__name__)

        self._cache[series_id] = (now, None)
        return None
