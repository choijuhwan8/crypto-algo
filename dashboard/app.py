import json, os
from pathlib import Path
from flask import Flask, render_template_string, jsonify
import urllib.request

app = Flask(__name__)
BASE = Path(__file__).parent.parent / "data"

def load_json(name):
    try:
        return json.loads((BASE / name).read_text())
    except Exception:
        return None

TEMPLATE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<title>Crypto Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0f1117;color:#e0e0e0;padding:20px}
  h1{font-size:1.4rem;margin-bottom:16px;color:#fff}
  h2{font-size:.85rem;text-transform:uppercase;color:#aaa;margin-bottom:10px}
  .cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}
  .card{background:#1a1d27;border-radius:8px;padding:16px 24px;min-width:140px;flex:1}
  .card .label{font-size:.7rem;text-transform:uppercase;color:#888;margin-bottom:4px}
  .card .value{font-size:1.6rem;font-weight:700}
  .pos{color:#26c17c} .neg{color:#e05252} .neu{color:#7eb6ff}
  .chart-box{background:#1a1d27;border-radius:8px;padding:16px;margin-bottom:24px}
  .chart-box h2{font-size:.85rem;color:#aaa;margin-bottom:12px;text-transform:uppercase}
  .charts-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:16px;margin-bottom:24px}
  table{width:100%;border-collapse:collapse;background:#1a1d27;border-radius:8px;overflow:hidden;margin-bottom:24px}
  th{background:#12151e;font-size:.7rem;text-transform:uppercase;color:#888;padding:8px 12px;text-align:left}
  td{padding:7px 12px;font-size:.8rem;border-top:1px solid #252836}
  tr:hover td{background:#20243a}
  .no-data{color:#888;font-style:italic;padding:20px 0;margin-bottom:24px}
  .live-price{font-weight:700;font-size:.9rem}
  .badge{font-size:.65rem;padding:2px 6px;border-radius:4px;background:#252836;color:#aaa;margin-left:4px}
  .tf-btns{display:flex;gap:6px;margin-bottom:12px}
  .tf-btn{background:#1a1d27;border:1px solid #252836;color:#aaa;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:.75rem}
  .tf-btn.active,.tf-btn:hover{background:#7eb6ff;color:#0f1117;border-color:#7eb6ff}
</style>
</head><body>
<h1>Crypto Paper Trading — Dashboard
  <span class="badge" id="last-refresh">refreshing...</span>
</h1>

{% if summary %}
<div class="cards">
  <div class="card"><div class="label">Equity</div>
    <div class="value neu">${{ "%.2f"|format(summary.equity) }}</div></div>
  <div class="card"><div class="label">Total Return</div>
    <div class="value {{ 'pos' if summary.total_return_pct >= 0 else 'neg' }}">
      {{ "%.2f"|format(summary.total_return_pct) }}%</div></div>
  <div class="card"><div class="label">Drawdown</div>
    <div class="value neg">{{ "%.2f"|format(summary.drawdown_pct) }}%</div></div>
  <div class="card"><div class="label">Win Rate</div>
    <div class="value {{ 'pos' if summary.win_rate >= 50 else 'neg' }}">
      {{ "%.1f"|format(summary.win_rate) }}%</div></div>
  <div class="card"><div class="label">Total Trades</div>
    <div class="value neu">{{ summary.total_trades }}</div></div>
</div>
{% else %}
<p class="no-data">No state data yet — waiting for bot to run.</p>
{% endif %}

<div class="chart-box">
  <h2>Equity Curve</h2>
  {% if equity_labels %}
  <canvas id="ec" height="80"></canvas>
  <script>
    new Chart(document.getElementById('ec'), {
      type: 'line',
      data: {
        labels: {{ equity_labels|tojson }},
        datasets:[{
          data: {{ equity_values|tojson }},
          borderColor:'#7eb6ff', borderWidth:1.5,
          pointRadius:0, fill:true,
          backgroundColor:'rgba(126,182,255,0.08)'
        }]
      },
      options:{
        plugins:{legend:{display:false}},
        scales:{
          x:{ticks:{maxTicksLimit:8,color:'#555'},grid:{color:'#1e2130'}},
          y:{ticks:{color:'#555'},grid:{color:'#1e2130'}}
        }
      }
    });
  </script>
  {% else %}
  <p class="no-data">No equity curve data yet.</p>
  {% endif %}
</div>

<h2>Live Price Charts</h2>
{% if active_symbols %}
<div class="tf-btns">
  <button class="tf-btn" data-tf="1m">1 min</button>
  <button class="tf-btn active" data-tf="15m">15 min</button>
  <button class="tf-btn" data-tf="1h">1 hour</button>
  <button class="tf-btn" data-tf="1d">1 day</button>
</div>
<div class="charts-grid">
  {% for sym in active_symbols %}
  <div class="chart-box">
    <h2>{{ sym }}/USDT
      <span class="live-price neu" id="price-{{ sym }}">loading...</span>
    </h2>
    <canvas id="chart-{{ sym }}" height="120"></canvas>
  </div>
  {% endfor %}
</div>
{% else %}
<p class="no-data">No open positions — no live charts.</p>
{% endif %}

<h2>Open Positions</h2>
{% if open_positions %}
<table>
  <thead><tr>
    <th>Pair</th><th>Direction</th>
    <th>Entry Z</th><th>Current Z</th>
    <th>Entry Price A</th><th>Live Price A</th><th>Leg A PnL</th>
    <th>Entry Price B</th><th>Live Price B</th><th>Leg B PnL</th>
    <th>Notional</th><th>Leverage</th><th>Total uPnL</th>
    <th>Stop Loss</th><th>Exit Target</th>
    <th>Opened</th><th>Last Updated</th>
  </tr></thead>
  <tbody>
  {% for p in open_positions %}
  {% set sl_pnl = -(p.get('notional_a',0) + p.get('notional_b',0)) * 0.15 %}
  <tr id="pos-{{ loop.index }}">
    <td>{{ p.get('pair_key','—') }}</td>
    <td>{{ p.get('direction','—') }}</td>
    <td>{{ "%.2f"|format(p.get('entry_zscore',0)) }}</td>
    <td>{{ "%.2f"|format(p.get('current_zscore',0)) if p.get('current_zscore') is not none else '—' }}</td>
    <td>${{ "%.4f"|format(p.get('entry_price_a',0)) }}</td>
    <td class="neu" id="live-a-{{ p.get('sym_a','') }}">${{ "%.4f"|format(p.get('current_price_a',0)) if p.get('current_price_a') else '—' }}</td>
    <td class="{{ 'pos' if p.get('pnl_a',0)>=0 else 'neg' }}" id="pnl-a-{{ loop.index }}">{{ "$%.2f"|format(p.get('pnl_a',0)) if p.get('pnl_a') is not none else '—' }}</td>
    <td>${{ "%.4f"|format(p.get('entry_price_b',0)) }}</td>
    <td class="neu" id="live-b-{{ p.get('sym_b','') }}">${{ "%.4f"|format(p.get('current_price_b',0)) if p.get('current_price_b') else '—' }}</td>
    <td class="{{ 'pos' if p.get('pnl_b',0)>=0 else 'neg' }}" id="pnl-b-{{ loop.index }}">{{ "$%.2f"|format(p.get('pnl_b',0)) if p.get('pnl_b') is not none else '—' }}</td>
    <td>${{ "%.2f"|format(p.get('notional_a',0)) }}</td>
    <td class="neu">{{ leverage }}x</td>
    <td class="{{ 'pos' if p.get('pnl',0)>=0 else 'neg' }}" id="pnl-total-{{ loop.index }}">${{ "%.2f"|format(p.get('pnl',0)) }}</td>
    <td class="neg">${{ "%.2f"|format(sl_pnl) }}</td>
    <td class="neu">z → 0.0</td>
    <td>{{ p.get('entry_time','—')[:19] }}</td>
    <td>{{ p.get('last_updated','—')[:19] if p.get('last_updated') else '—' }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="no-data">No open positions.</p>
{% endif %}

<h2>Recent Trades (last 20)</h2>
{% if trades %}
<table>
  <thead><tr>
    <th>Pair</th><th>Direction</th><th>Entry Z</th><th>PnL</th><th>Reason</th><th>Opened</th><th>Closed</th>
  </tr></thead>
  <tbody>
  {% for t in trades|reverse %}
  <tr>
    <td>{{ t.get('pair_key','—') }}</td>
    <td>{{ t.get('direction','—') }}</td>
    <td>{{ "%.2f"|format(t.get('entry_zscore',0)) }}</td>
    <td class="{{ 'pos' if t.get('realized_pnl',0)>=0 else 'neg' }}">
      ${{ "%.2f"|format(t.get('realized_pnl',0)) }}</td>
    <td>{{ t.get('reason','—') }}</td>
    <td>{{ t.get('entry_time','—')[:19] }}</td>
    <td>{{ t.get('exit_time','—')[:19] }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="no-data">No closed trades yet.</p>
{% endif %}

{% if active_symbols %}
<script>
// --- Live price charts ---
const symbols = {{ active_symbols|tojson }};
const COLORS = ['#7eb6ff','#26c17c','#f5a623','#e05252','#b48eff','#50e3c2'];
const charts = {};
// Store full history as {ts: ms timestamp, price: float}
const priceHistory = {};
// Timeframe window in ms
const WINDOWS = { '1m': 60e3, '15m': 15*60e3, '1h': 3600e3, '1d': 86400e3 };
let activeWindow = '15m';

function visiblePoints(sym) {
  const cutoff = Date.now() - WINDOWS[activeWindow];
  return priceHistory[sym].filter(p => p.ts >= cutoff);
}

function updateChart(sym) {
  const pts = visiblePoints(sym);
  const labels = pts.map(p => new Date(p.ts).toLocaleTimeString());
  const data = pts.map(p => p.price);
  charts[sym].data.labels = labels;
  charts[sym].data.datasets[0].data = data;
  charts[sym].update();
}

// Init charts
symbols.forEach((sym, i) => {
  priceHistory[sym] = [];
  const ctx = document.getElementById('chart-' + sym);
  if (!ctx) return;
  charts[sym] = new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [{ label: sym, data: [],
      borderColor: COLORS[i % COLORS.length], borderWidth: 1.5,
      pointRadius: 0, fill: false }] },
    options: {
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, color: '#555' }, grid: { color: '#1e2130' } },
        y: { ticks: { color: '#555' }, grid: { color: '#1e2130' } }
      }
    }
  });
});

// Timeframe buttons
document.querySelectorAll('.tf-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    activeWindow = btn.dataset.tf;
    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    symbols.forEach(updateChart);
  });
});

// Fetch prices from our backend every 5s
async function fetchPrices() {
  try {
    const res = await fetch('/api/prices?symbols=' + symbols.join(','));
    const data = await res.json();
    const now = new Date().toLocaleTimeString();

    symbols.forEach(sym => {
      const price = data[sym];
      if (!price) return;

      // Update price label
      const el = document.getElementById('price-' + sym);
      if (el) el.textContent = '$' + parseFloat(price).toPrecision(6);

      // Update live price cells in table
      const cellA = document.getElementById('live-a-' + sym);
      if (cellA) cellA.textContent = '$' + parseFloat(price).toFixed(4);
      const cellB = document.getElementById('live-b-' + sym);
      if (cellB) cellB.textContent = '$' + parseFloat(price).toFixed(4);

      // Store timestamped point, keep max 1 day of 5s data (~17280 pts)
      priceHistory[sym].push({ ts: Date.now(), price: parseFloat(price) });
      if (priceHistory[sym].length > 17280) priceHistory[sym].shift();

      // Update chart for active window
      if (charts[sym]) updateChart(sym);
    });

    document.getElementById('last-refresh').textContent =
      'live · updated ' + now;
  } catch(e) {
    document.getElementById('last-refresh').textContent = 'price fetch failed';
  }
}

fetchPrices();
setInterval(fetchPrices, 5000);
</script>
{% endif %}

</body></html>"""


@app.route("/api/prices")
def api_prices():
    """Fetch live prices from Binance public ticker for requested symbols."""
    symbols_param = request_symbols()
    result = {}
    for sym in symbols_param:
        try:
            url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read())
                result[sym] = data.get("price")
        except Exception:
            result[sym] = None
    return jsonify(result)


def request_symbols():
    from flask import request
    raw = request.args.get("symbols", "")
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


@app.route("/")
def index():
    state = load_json("state.json")
    equity_data = load_json("equity_curve.json")

    if state:
        closed = (state.get("closed_positions") or [])[-20:]
        trades = closed
        open_positions = state.get("open_positions") or []
        from src.config import INITIAL_CAPITAL, LEVERAGE
        equity = state.get("equity", INITIAL_CAPITAL)
        peak = state.get("peak_equity", equity)
        n = len(state.get("closed_positions") or [])
        wins = sum(1 for t in (state.get("closed_positions") or []) if t.get("realized_pnl", 0) > 0)
        summary = {
            "equity": equity,
            "total_return_pct": round((equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 2),
            "drawdown_pct": round((peak - equity) / peak * 100, 2) if peak else 0.0,
            "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "total_trades": n,
        }
    else:
        summary, trades, open_positions = None, None, []
        LEVERAGE = 3

    # Collect unique symbols from open positions dynamically
    active_symbols = list(dict.fromkeys(
        sym
        for p in open_positions
        for sym in [p.get("sym_a"), p.get("sym_b")]
        if sym
    ))

    equity_labels, equity_values = [], []
    if equity_data:
        pts = equity_data[-200:]
        equity_labels = [p.get("ts", p.get("time", i)) for i, p in enumerate(pts)]
        equity_values = [p.get("equity", p.get("value", 0)) for p in pts]

    return render_template_string(
        TEMPLATE,
        summary=summary,
        trades=trades,
        open_positions=open_positions,
        active_symbols=active_symbols,
        leverage=LEVERAGE,
        equity_labels=equity_labels,
        equity_values=equity_values,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
