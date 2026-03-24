from dotenv import load_dotenv; load_dotenv()
from src.data_service import DataService
from src.config import CORR_LOG_PRICE_MIN, CORR_RETURN_MIN, COINT_PVALUE_MAX, IC_THRESHOLD, ROLLING_WINDOW
import numpy as np
from scipy import stats
from statsmodels.tsa.stattools import adfuller
import itertools

ds = DataService()
tokens = ds.get_available_tokens()
print("Warming up...")
ds.warmup(tokens)

all_pairs = list(itertools.combinations(tokens, 2))

def compute_ic(log_a, log_b, beta, alpha, window=ROLLING_WINDOW, horizon=24):
    spread = log_a - (alpha + beta * log_b)
    win = min(window, len(spread))
    spread_mean = spread.rolling(win).mean()
    spread_std = spread.rolling(win).std()
    zscore = (spread - spread_mean) / spread_std
    future_ret = spread.shift(-horizon) - spread
    valid = zscore.dropna().index.intersection(future_ret.dropna().index)
    if len(valid) < 100:
        return None
    ic = float(zscore.loc[valid].corr(future_ret.loc[valid]))
    return ic if not np.isnan(ic) else None

print(f"\n{'Pair':<20} {'corr_p':>7} {'corr_r':>7} {'adf_p':>7} {'IC':>8} {'valid_pts':>10}")
print("-" * 70)

for tok_a, tok_b in all_pairs:
    df_a = ds.get_cache(tok_a)
    df_b = ds.get_cache(tok_b)
    if df_a is None or df_b is None:
        continue

    common = df_a.index.intersection(df_b.index)
    if len(common) < 500:
        continue

    log_a = np.log(df_a.loc[common, "close"])
    log_b = np.log(df_b.loc[common, "close"])

    corr_price = float(log_a.corr(log_b))
    corr_return = float(log_a.diff().dropna().corr(log_b.diff().dropna()))

    if np.isnan(corr_price) or corr_price < CORR_LOG_PRICE_MIN:
        continue
    if np.isnan(corr_return) or corr_return < CORR_RETURN_MIN:
        continue
    if log_b.nunique() < 2 or log_a.nunique() < 2:
        continue

    slope, intercept, *_ = stats.linregress(log_b.values, log_a.values)
    spread = log_a.values - (intercept + slope * log_b.values)
    if len(set(spread)) < 2:
        continue

    try:
        _, adf_pval, *_ = adfuller(spread, maxlag=1, autolag=None)
    except Exception:
        continue

    if adf_pval >= COINT_PVALUE_MAX:
        continue

    # compute IC and valid point count
    spread_s = log_a - (intercept + slope * log_b)
    win = min(ROLLING_WINDOW, len(spread_s))
    zscore = (spread_s - spread_s.rolling(win).mean()) / spread_s.rolling(win).std()
    future_ret = spread_s.shift(-24) - spread_s
    valid = zscore.dropna().index.intersection(future_ret.dropna().index)
    valid_pts = len(valid)
    ic = compute_ic(log_a, log_b, float(slope), float(intercept))
    ic_str = f"{ic:.4f}" if ic is not None else "N/A (< 100 pts)"

    print(f"{tok_a+'-'+tok_b:<20} {corr_price:>7.3f} {corr_return:>7.3f} {adf_pval:>7.4f} {ic_str:>8} {valid_pts:>10}")

print(f"\nROLLING_WINDOW={ROLLING_WINDOW}h, IC_THRESHOLD={IC_THRESHOLD}")
print(f"Bars loaded per token: ~1000 (~41 days)")
print(f"Need at least ROLLING_WINDOW + 124 bars = {ROLLING_WINDOW + 124} bars for IC to work")
