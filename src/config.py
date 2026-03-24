"""
Central configuration loaded from environment variables.
Copy .env.example to .env and fill in your values before running.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Exchange
# ---------------------------------------------------------------------------
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
# Set BINANCE_TESTNET=false only when you are ready for live paper on mainnet
BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Your personal chat ID (get it from @userinfobot)
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Capital & sizing
# ---------------------------------------------------------------------------
INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "10000"))
LEVERAGE: int = int(os.getenv("LEVERAGE", "3"))
MAX_CONCURRENT_PAIRS: int = int(os.getenv("MAX_CONCURRENT_PAIRS", "5"))
# Maximum fraction of equity to allocate per pair (before leverage)
MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.20"))

# ---------------------------------------------------------------------------
# Risk limits
# ---------------------------------------------------------------------------
MAX_PORTFOLIO_DD: float = float(os.getenv("MAX_PORTFOLIO_DD", "0.25"))   # 25%
STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "0.15"))         # 15% per trade
# Kill switch if latest candle timestamp is older than this many minutes
MAX_DATA_STALENESS_MIN: int = int(os.getenv("MAX_DATA_STALENESS_MIN", "90"))

# ---------------------------------------------------------------------------
# Signal / strategy
# ---------------------------------------------------------------------------
Z_ENTRY: float = float(os.getenv("Z_ENTRY", "1.5"))
Z_EXIT: float = float(os.getenv("Z_EXIT", "0.0"))
# Rolling regression window in hours (6 months ≈ 4 320 h)
ROLLING_WINDOW: int = int(os.getenv("ROLLING_WINDOW", "4320"))
FEE_BPS: float = float(os.getenv("FEE_BPS", "4.5"))    # Binance VIP-0 + BNB discount
TIMEFRAME: str = os.getenv("TIMEFRAME", "1h")

# ---------------------------------------------------------------------------
# Pair-selection thresholds (daily recalc)
# ---------------------------------------------------------------------------
CORR_LOG_PRICE_MIN: float = float(os.getenv("CORR_LOG_PRICE_MIN", "0.90"))
CORR_RETURN_MIN: float = float(os.getenv("CORR_RETURN_MIN", "0.50"))
COINT_PVALUE_MAX: float = float(os.getenv("COINT_PVALUE_MAX", "0.05"))
# IC must be ≤ this value (negative = mean-reverting)
IC_THRESHOLD: float = float(os.getenv("IC_THRESHOLD", "-0.10"))
TOP_N_PAIRS: int = int(os.getenv("TOP_N_PAIRS", "5"))

# ---------------------------------------------------------------------------
# Candidate token universe (from notebook)
# ---------------------------------------------------------------------------
CANDIDATE_TOKENS: list = [
    t.strip() for t in os.getenv(
        "CANDIDATE_TOKENS",
        "BTC,ETH,SOL,ADA,AVAX,DOT,BAND,NKN,ANKR,ONT,CTSI,STORJ,ENJ,ZRX,ATA,"
        "DOGE,MATIC,LINK,UNI,ATOM"
    ).split(",")
]

# ---------------------------------------------------------------------------
# Data / paths
# ---------------------------------------------------------------------------
WARMUP_HOURS: int = int(os.getenv("WARMUP_HOURS", "4320"))
DATA_DIR: str = "data"
LOG_DIR: str = "logs"
STATE_FILE: str = os.path.join(DATA_DIR, "state.json")
RUN_LOG_FILE: str = "run_log.csv"
