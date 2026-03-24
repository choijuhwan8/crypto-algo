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
print(f"\nTop 15 pairs by corr_price (regardless of threshold):\n")
print(f"{'Pair':<20} {'corr_p':>7} {'corr_r':>7} {'bars':>6}")
print("-" * 45)

results = []
for tok_a, tok_b in all_pairs:
    df_a = ds.get_cache(tok_a)
    df_b = ds.get_cache(tok_b)
    if df_a is None or df_b is None:
        continue
    common = df_a.index.intersection(df_b.index)
    if len(common) < 100:
        continue
    log_a = np.log(df_a.loc[common, "close"])
    log_b = np.log(df_b.loc[common, "close"])
    corr_price = float(log_a.corr(log_b))
    corr_return = float(log_a.diff().dropna().corr(log_b.diff().dropna()))
    results.append((tok_a, tok_b, corr_price, corr_return, len(common)))

results.sort(key=lambda x: x[2], reverse=True)
for tok_a, tok_b, cp, cr, bars in results[:15]:
    print(f"{tok_a+'-'+tok_b:<20} {cp:>7.3f} {cr:>7.3f} {bars:>6}")

print(f"\nCORR_LOG_PRICE_MIN={CORR_LOG_PRICE_MIN}, CORR_RETURN_MIN={CORR_RETURN_MIN}")
