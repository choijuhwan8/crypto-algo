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
print(f"Testing {len(all_pairs)} pairs\n")

fail_corr_price = fail_corr_return = fail_constant = fail_coint = fail_ic = passed = 0

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
    ret_a = log_a.diff().dropna()
    ret_b = log_b.diff().dropna()
    corr_return = float(ret_a.corr(ret_b))

    if np.isnan(corr_price) or corr_price < CORR_LOG_PRICE_MIN:
        fail_corr_price += 1
        continue

    if np.isnan(corr_return) or corr_return < CORR_RETURN_MIN:
        fail_corr_return += 1
        continue

    if log_b.nunique() < 2 or log_a.nunique() < 2:
        fail_constant += 1
        continue

    slope, intercept, *_ = stats.linregress(log_b.values, log_a.values)
    spread = log_a.values - (intercept + slope * log_b.values)
    if len(set(spread)) < 2:
        fail_constant += 1
        continue

    try:
        adf_stat, adf_pval, *_ = adfuller(spread, maxlag=1, autolag=None)
    except Exception:
        fail_constant += 1
        continue

    if adf_pval >= COINT_PVALUE_MAX:
        fail_coint += 1
        continue

    passed += 1
    print(f"PASSED coint: {tok_a}-{tok_b} | corr_price={corr_price:.3f} corr_ret={corr_return:.3f} adf_p={adf_pval:.4f}")

print(f"\n--- Filter Summary ---")
print(f"Failed corr_price (<{CORR_LOG_PRICE_MIN}): {fail_corr_price}")
print(f"Failed corr_return (<{CORR_RETURN_MIN}):  {fail_corr_return}")
print(f"Failed constant check:                    {fail_constant}")
print(f"Failed cointegration (p>={COINT_PVALUE_MAX}):   {fail_coint}")
print(f"Passed cointegration (before IC):         {passed}")
