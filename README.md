# crypto-algo — Mean Reversion Paper Trading Bot

Automated pairs trading bot running on Binance Futures Testnet with Telegram alerts and a web dashboard.

---

## Stack
- **Exchange**: Binance Futures Testnet (via ccxt)
- **Strategy**: Mean reversion on cointegrated pairs (z-score entry/exit)
- **Alerts**: Telegram bot
- **Dashboard**: Flask web app
- **Hosting**: AWS EC2 (Ubuntu 24.04)

---

## First-Time Setup (AWS EC2)

### 1. SSH into your server
```bash
ssh -i ~/.ssh/cryptobot-key.pem ubuntu@13.214.165.240
```

### 2. Install dependencies
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git
```

### 3. Clone the repo
```bash
git clone https://github.com/choijuhwan8/crypto-algo.git
cd crypto-algo
```

### 4. Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 5. Create your `.env` file
```bash
nano .env
```
Fill in your credentials (see `.env.example` for all variables):
```
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
BINANCE_TESTNET=true
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
INITIAL_CAPITAL=10000
ROLLING_WINDOW=2160
WARMUP_HOURS=2160
```

---

## Running the Bot (24/7)

The bot runs as a systemd service — it auto-starts on reboot and restarts on crash.

```bash
# Install and start
sudo cp cryptobot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cryptobot
sudo systemctl start cryptobot

# Check status
sudo systemctl status cryptobot

# View live logs
sudo journalctl -u cryptobot -f

# Stop / restart
sudo systemctl stop cryptobot
sudo systemctl restart cryptobot
```

---

## Running the Dashboard

The dashboard runs on port 5000 and auto-refreshes every 60 seconds.

### First time — open port 5000 in AWS:
1. AWS Console → EC2 → Instances → your instance
2. Security tab → click the security group
3. Edit inbound rules → Add rule:
   - Type: Custom TCP, Port: 5000, Source: My IP
4. Save rules

### Start the dashboard:
```bash
cd ~/crypto-algo
source venv/bin/activate
python dashboard/app.py &
```

### Access in browser:
```
http://13.214.165.240:5000
```

### Stop the dashboard:
```bash
kill $(lsof -t -i:5000)
```

---

## Updating the Bot

When new code is pushed to GitHub:
```bash
ssh -i ~/.ssh/cryptobot-key.pem ubuntu@13.214.165.240
cd ~/crypto-algo
git pull
sudo systemctl restart cryptobot
```

---

## Monitoring

| Method | How |
|---|---|
| Telegram | Bot sends alerts for trades, errors, daily PnL |
| Dashboard | `http://13.214.165.240:5000` |
| Live logs | `sudo journalctl -u cryptobot -f` |
| State file | `cat ~/crypto-algo/data/state.json` |
| Order log | `cat ~/crypto-algo/data/orders.jsonl` |
| Run log | `cat ~/crypto-algo/run_log.csv` |

---

## Scheduler

| Job | Frequency |
|---|---|
| Signal check & execution | Every hour |
| Daily PnL report | Every 24h |
| Pair reselection & rebalance | Every week |
| Validation report | Every month |

---

## Key Config Variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `INITIAL_CAPITAL` | 10000 | Virtual capital in USD |
| `LEVERAGE` | 3 | Futures leverage |
| `ROLLING_WINDOW` | 2160 | Rolling window in hours (90 days) |
| `Z_ENTRY` | 1.5 | Z-score threshold to open trade |
| `Z_EXIT` | 0.0 | Z-score threshold to close trade |
| `MAX_PORTFOLIO_DD` | 0.25 | Kill switch at 25% drawdown |
| `STOP_LOSS_PCT` | 0.15 | Per-trade stop loss |
| `BINANCE_TESTNET` | true | Set false for mainnet |
