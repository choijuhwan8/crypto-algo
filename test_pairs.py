"""
test_pairs.py — Full pair-selection pipeline debug script.

Mirrors PairSelector._evaluate_pair() exactly, printing results at each stage:
  Stage 1: Correlation filter  (corr_price >= 0.90, corr_return >= 0.50)
  Stage 2: Cointegration test  (ADF p-value < 0.05)
  Stage 3: IC filter           (IC <= -0.10)
  Final:   Top pairs ranked by IC
"""
from dotenv import load_dotenv; load_dotenv()

import itertools
import numpy as np
from scipy import stats
from statsmodels.tsa.stattools import adfuller

from src.data_service import DataService
from src.config import (
    CORR_LOG_PRICE_MIN, CORR_RETURN_MIN,
    COINT_PVALUE_MAX, IC_THRESHOLD,
    ROLLING_WINDOW, TOP_N_PAIRS,
)

# ── Data warmup ──────────────────────────────────────────────────────────────
ds = DataService()
tokens = ds.get_available_tokens()
print(f"Tokens available: {len(tokens)}")
print("Warming up data (this may take a few minutes)...")
ds.warmup(tokens)

# Verify data loaded
loaded = [t for t in tokens if ds.get_cache(t) is not None]
print(f"Tokens with data: {len(loaded)}")
sample = ds.get_cache(loaded[0]) if loaded else None
if sample is not None:
    print(f"Sample bars ({loaded[0]}): {len(sample)}  "
          f"[{sample.index[0]} → {sample.index[-1]}]\n")

# ── Pipeline ─────────────────────────────────────────────────────────────────
all_pairs = list(itertools.combinations(loaded, 2))
print(f"Total pairs to test: {len(all_pairs)}\n")

stage1_pass = []   # passed corr filter
stage2_pass = []   # passed cointegration
stage3_pass = []   # passed IC filter (final candidates)

SEP = "-" * 70

# ── Stage 1: Correlation ─────────────────────────────────────────────────────
print("=" * 70)
print(f"STAGE 1 — Correlation filter  "
      f"(corr_price >= {CORR_LOG_PRICE_MIN}, corr_return >= {CORR_RETURN_MIN})")
print("=" * 70)
print(f"{'Pair':<22} {'corr_price':>10} {'corr_return':>11} {'bars':>6}  result")
print(SEP)

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

    corr_price  = float(log_a.corr(log_b))
    corr_return = float(log_a.diff().dropna().corr(log_b.diff().dropna()))

    passed = corr_price >= CORR_LOG_PRICE_MIN and corr_return >= CORR_RETURN_MIN
    label  = "PASS" if passed else "fail"
    print(f"{tok_a+'-'+tok_b:<22} {corr_price:>10.4f} {corr_return:>11.4f} "
          f"{len(common):>6}  {label}")

    if passed:
        stage1_pass.append((tok_a, tok_b, log_a, log_b))

print(f"\n→ Stage 1 passed: {len(stage1_pass)} / {len(all_pairs)} pairs\n")

# ── Stage 2: Cointegration (ADF) ─────────────────────────────────────────────
print("=" * 70)
print(f"STAGE 2 — Cointegration test  (ADF p-value < {COINT_PVALUE_MAX})")
print("=" * 70)
print(f"{'Pair':<22} {'beta':>7} {'adf_stat':>9} {'adf_pval':>9}  result")
print(SEP)

for tok_a, tok_b, log_a, log_b in stage1_pass:
    if log_b.nunique() < 2 or log_a.nunique() < 2:
        continue
    slope, intercept, *_ = stats.linregress(log_b.values, log_a.values)
    spread = log_a.values - (intercept + slope * log_b.values)
    if len(set(spread)) < 2:
        continue
    adf_stat, adf_pval, *_ = adfuller(spread, maxlag=1, autolag=None)

    passed = adf_pval < COINT_PVALUE_MAX
    label  = "PASS" if passed else "fail"
    print(f"{tok_a+'-'+tok_b:<22} {slope:>7.4f} {adf_stat:>9.4f} "
          f"{adf_pval:>9.4f}  {label}")

    if passed:
        stage2_pass.append((tok_a, tok_b, log_a, log_b, slope, intercept, adf_pval))

print(f"\n→ Stage 2 passed: {len(stage2_pass)} / {len(stage1_pass)} pairs\n")

# ── Stage 3: IC filter ────────────────────────────────────────────────────────
print("=" * 70)
print(f"STAGE 3 — IC filter  (IC <= {IC_THRESHOLD})")
print("=" * 70)
print(f"{'Pair':<22} {'ic':>7} {'valid_pts':>9}  result")
print(SEP)

for tok_a, tok_b, log_a, log_b, slope, intercept, adf_pval in stage2_pass:
    spread = log_a - (intercept + slope * log_b)
    win    = min(ROLLING_WINDOW, len(spread))
    zscore = (spread - spread.rolling(win).mean()) / spread.rolling(win).std()
    future_ret = spread.shift(-24) - spread

    valid = zscore.dropna().index.intersection(future_ret.dropna().index)
    if len(valid) < 100:
        print(f"{tok_a+'-'+tok_b:<22} {'N/A':>7} {len(valid):>9}  fail (too few points)")
        continue

    ic = float(zscore.loc[valid].corr(future_ret.loc[valid]))
    if np.isnan(ic):
        ic = 0.0

    passed = ic <= IC_THRESHOLD
    label  = "PASS" if passed else "fail"
    print(f"{tok_a+'-'+tok_b:<22} {ic:>7.4f} {len(valid):>9}  {label}")

    if passed:
        stage3_pass.append({
            "sym_a":       tok_a,
            "sym_b":       tok_b,
            "beta":        round(float(slope), 4),
            "alpha":       round(float(intercept), 4),
            "adf_pvalue":  round(float(adf_pval), 4),
            "ic":          round(ic, 4),
        })

print(f"\n→ Stage 3 passed: {len(stage3_pass)} / {len(stage2_pass)} pairs\n")

# ── Final: Top N pairs ────────────────────────────────────────────────────────
print("=" * 70)
print(f"FINAL — Top {TOP_N_PAIRS} pairs (ranked by IC ascending)")
print("=" * 70)
stage3_pass.sort(key=lambda x: x["ic"])
selected = stage3_pass[:TOP_N_PAIRS]

if selected:
    print(f"{'Pair':<22} {'beta':>7} {'adf_pval':>9} {'ic':>7}")
    print(SEP)
    for p in selected:
        print(f"{p['sym_a']+'-'+p['sym_b']:<22} {p['beta']:>7.4f} "
              f"{p['adf_pvalue']:>9.4f} {p['ic']:>7.4f}")
else:
    print("No pairs passed all filters.")

print(f"\nConfig thresholds: corr_price>={CORR_LOG_PRICE_MIN}, "
      f"corr_return>={CORR_RETURN_MIN}, adf_pval<{COINT_PVALUE_MAX}, "
      f"ic<={IC_THRESHOLD}, top_n={TOP_N_PAIRS}")
