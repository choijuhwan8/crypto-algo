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
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3/dist/chartjs-plugin-annotation.min.js"></script>
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

<h2>Open Positions</h2>
{% if open_positions %}
<div class="tf-btns">
  <button class="tf-btn" data-tf="1m">1 min</button>
  <button class="tf-btn active" data-tf="15m">15 min</button>
  <button class="tf-btn" data-tf="1h">1 hour</button>
  <button class="tf-btn" data-tf="1d">1 day</button>
  <button class="tf-btn" data-tf="1w">1 week</button>
  <button class="tf-btn" data-tf="1M">1 month</button>
</div>
<table>
  <thead><tr>
    <th></th>
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
  {% set pair_key = p.get('pair_key','') %}
  {% set direction = p.get('direction','') %}
  {% set sl_pnl = -(p.get('notional_a',0) + p.get('notional_b',0)) * 0.15 %}
  <tr class="pos-row" onclick="toggleCharts('charts-{{ loop.index }}')" style="cursor:pointer"
    data-sym-a="{{ p.get('sym_a','') }}"
    data-sym-b="{{ p.get('sym_b','') }}"
    data-entry-a="{{ p.get('entry_price_a',0) }}"
    data-entry-b="{{ p.get('entry_price_b',0) }}"
    data-notional-a="{{ p.get('notional_a',0) }}"
    data-notional-b="{{ p.get('notional_b',0) }}"
    data-direction="{{ direction }}"
    data-idx="{{ loop.index }}">
    <td style="color:#555;font-size:.7rem">▶</td>
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
  <tr id="charts-{{ loop.index }}" style="display:none">
    <td colspan="18" style="padding:16px;background:#12151e">
      <div class="charts-grid">
        {% for leg in ['a','b'] %}
          {% set sym = p.get('sym_'+leg) %}
          {% set is_a = leg == 'a' %}
          {% set entry_price = p.get('entry_price_'+leg) %}
          {% set leg_dir = ('LONG' if (direction=='LONG_SPREAD' and is_a) or (direction=='SHORT_SPREAD' and not is_a) else 'SHORT') %}
          {% set chart_id = 'chart-' + pair_key + '-' + leg %}
        <div class="chart-box" style="margin-bottom:0">
          <h2>{{ sym }}/USDT
            <span class="live-price neu" id="price-{{ pair_key }}-{{ leg }}">loading...</span>
            <span class="badge {{ 'pos' if leg_dir=='LONG' else 'neg' }}">{{ leg_dir }}</span>
          </h2>
          <canvas id="{{ chart_id }}" height="120"
            data-sym="{{ sym }}"
            data-entry="{{ entry_price or '' }}"
            data-dir="{{ leg_dir }}">
          </canvas>
        </div>
        {% endfor %}
      </div>
    </td>
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

{% if open_positions %}
<script>
function toggleCharts(rowId) {
  const row = document.getElementById(rowId);
  const posRow = row.previousElementSibling;
  const arrow = posRow.querySelector('td:first-child');
  if (row.style.display === 'none') {
    row.style.display = '';
    arrow.textContent = '▼';
    // Trigger chart init for newly visible canvases
    row.querySelectorAll('canvas[data-sym]').forEach(canvas => {
      if (!registry[canvas.id]) initChart(canvas);
      loadHistory(canvas.id, activeWindow);
    });
  } else {
    row.style.display = 'none';
    arrow.textContent = '▶';
  }
}

// --- Live price charts (one per position leg) ---
const COLORS = ['#7eb6ff','#26c17c','#f5a623','#e05252','#b48eff','#50e3c2'];
const WINDOWS = { '1m': 60e3, '15m': 15*60e3, '1h': 3600e3, '1d': 86400e3, '1w': 7*86400e3, '1M': 30*86400e3 };
const TF_FORMATS = {
  '1m':  { unit: 'minute',  fmt: 'HH:mm' },
  '15m': { unit: 'minute',  fmt: 'HH:mm' },
  '1h':  { unit: 'hour',    fmt: 'HH:mm' },
  '1d':  { unit: 'hour',    fmt: 'MMM d' },
  '1w':  { unit: 'day',     fmt: 'MMM d' },
  '1M':  { unit: 'day',     fmt: 'MMM d' },
};
let activeWindow = '15m';

// Build chart registry keyed by chartId
// chartId = "chart-{pair_key}-{a|b}", each has { sym, entryPrice, legDir, history, chart }
const registry = {};

let chartColorIndex = 0;
function initChart(canvas) {
  const chartId = canvas.id;
  if (registry[chartId]) return;
  const sym = canvas.dataset.sym;
  const entryPrice = parseFloat(canvas.dataset.entry) || null;
  const legDir = canvas.dataset.dir;
  const color = COLORS[chartColorIndex++ % COLORS.length];

  const annotations = {};
  if (entryPrice) {
    annotations.entryLine = {
      type: 'line', yMin: entryPrice, yMax: entryPrice,
      borderColor: '#f5a623', borderWidth: 1.5, borderDash: [6, 3],
      label: { display: true, content: 'Entry $' + entryPrice.toPrecision(5),
        position: 'start', color: '#f5a623',
        backgroundColor: 'rgba(245,166,35,0.15)', font: { size: 10 } }
    };
  }
  annotations.currentLine = {
    type: 'line', yMin: 0, yMax: 0,
    borderColor: legDir === 'LONG' ? '#26c17c' : '#e05252',
    borderWidth: 1, borderDash: [3, 3],
    label: { display: false, content: 'Now', position: 'end', color: '#aaa',
      backgroundColor: 'rgba(0,0,0,0.4)', font: { size: 10 } }
  };

  const chart = new Chart(canvas, {
    type: 'line',
    data: { datasets: [{ data: [], borderColor: color, borderWidth: 1.5,
      pointRadius: 0, fill: false }] },
    options: {
      animation: false, parsing: false,
      plugins: { legend: { display: false }, annotation: { annotations } },
      scales: {
        x: {
          type: 'time',
          time: { tooltipFormat: 'HH:mm:ss', displayFormats: {
            millisecond: 'HH:mm:ss', second: 'HH:mm:ss',
            minute: 'HH:mm', hour: 'HH:mm', day: 'MMM d',
          }},
          ticks: { color: '#555', maxTicksLimit: 8, maxRotation: 0 },
          grid: { color: '#1e2130' }
        },
        y: { ticks: { color: '#555' }, grid: { color: '#1e2130' } }
      }
    }
  });

  registry[chartId] = { sym, entryPrice, legDir, history: [], chart };
}

// Charts are hidden by default — init happens on row click
// No auto-init on page load

function updateChart(chartId) {
  const r = registry[chartId];
  const cutoff = Date.now() - WINDOWS[activeWindow];
  const pts = r.history.filter(p => p.ts >= cutoff);
  r.chart.data.datasets[0].data = pts.map(p => ({ x: p.ts, y: p.price }));
  if (pts.length > 0) {
    const cur = pts[pts.length - 1].price;
    const ann = r.chart.options.plugins.annotation.annotations;
    ann.currentLine.yMin = cur;
    ann.currentLine.yMax = cur;
    ann.currentLine.label.display = true;
    ann.currentLine.label.content = 'Now $' + cur.toPrecision(5);
  }
  r.chart.update();
}

async function loadHistory(chartId, window) {
  const r = registry[chartId];
  try {
    const res = await fetch(`/api/history?symbol=${r.sym}&window=${window}`);
    const rows = await res.json();
    rows.forEach(row => {
      if (!r.history.find(p => p.ts === row.ts))
        r.history.push({ ts: row.ts, price: row.price });
    });
    r.history.sort((a, b) => a.ts - b.ts);
    updateChart(chartId);
  } catch(e) {}
}

// Timeframe buttons
document.querySelectorAll('.tf-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    activeWindow = btn.dataset.tf;
    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    // Update x-axis format for all open charts
    const tf = TF_FORMATS[activeWindow] || TF_FORMATS['15m'];
    Object.values(registry).forEach(r => {
      r.chart.options.scales.x.time.unit = tf.unit;
      r.chart.options.scales.x.time.displayFormats = { [tf.unit]: tf.fmt };
    });
    Object.keys(registry).forEach(id => loadHistory(id, activeWindow));
  });
});

// On load: seed all charts with history (registry empty here; called after row click instead)

// Symbols from all position legs — collected from DOM so no dependency on registry
const allSyms = [...new Set(
  Array.from(document.querySelectorAll('canvas[data-sym]')).map(c => c.dataset.sym)
)];

async function fetchPrices() {
  if (!allSyms.length) return;
  try {
    const res = await fetch('/api/prices?symbols=' + allSyms.join(','));
    const data = await res.json();
    const now = new Date().toLocaleTimeString();

    // Update live price cells in table (always, even if chart not open)
    allSyms.forEach(sym => {
      const price = data[sym];
      if (!price) return;
      const p = parseFloat(price);
      const cellA = document.getElementById('live-a-' + sym);
      if (cellA) cellA.textContent = '$' + p.toFixed(4);
      const cellB = document.getElementById('live-b-' + sym);
      if (cellB) cellB.textContent = '$' + p.toFixed(4);
    });

    // Recompute live uPnL per position row
    document.querySelectorAll('tr.pos-row[data-idx]').forEach(row => {
      const symA = row.dataset.symA;
      const symB = row.dataset.symB;
      const priceA = parseFloat(data[symA]);
      const priceB = parseFloat(data[symB]);
      if (!priceA || !priceB) return;

      const entryA   = parseFloat(row.dataset.entryA);
      const entryB   = parseFloat(row.dataset.entryB);
      const notlA    = parseFloat(row.dataset.notionalA);
      const notlB    = parseFloat(row.dataset.notionalB);
      const dir      = row.dataset.direction;
      const idx      = row.dataset.idx;

      let pnlA, pnlB;
      if (dir === 'LONG_SPREAD') {
        pnlA = notlA * (priceA - entryA) / entryA;   // long leg A
        pnlB = notlB * (entryB - priceB) / entryB;   // short leg B
      } else {
        pnlA = notlA * (entryA - priceA) / entryA;   // short leg A
        pnlB = notlB * (priceB - entryB) / entryB;   // long leg B
      }
      const total = pnlA + pnlB;

      function fmt(v) { return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2); }
      function setCell(id, v) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = fmt(v);
        el.className = v >= 0 ? 'pos' : 'neg';
      }
      setCell('pnl-a-' + idx, pnlA);
      setCell('pnl-b-' + idx, pnlB);
      setCell('pnl-total-' + idx, total);
    });

    // Update open charts
    Object.entries(registry).forEach(([chartId, r]) => {
      const price = data[r.sym];
      if (!price) return;
      const p = parseFloat(price);

      // Update price label in chart header
      // chartId = "chart-{pair_key}-{a|b}", price el id = "price-{pair_key}-{a|b}"
      const priceEl = document.getElementById('price-' + chartId.replace(/^chart-/, ''));
      if (priceEl) priceEl.textContent = '$' + p.toPrecision(6);

      // Append to history
      r.history.push({ ts: Date.now(), price: p });
      if (r.history.length > 17280) r.history.shift();
      updateChart(chartId);
    });

    document.getElementById('last-refresh').textContent = 'live · updated ' + now;
  } catch(e) {
    document.getElementById('last-refresh').textContent = 'price fetch failed';
  }
}

fetchPrices();
setInterval(fetchPrices, 5000);
</script>
{% endif %}

</body></html>"""


@app.route("/api/history")
def api_history():
    """Fetch Binance kline history for a symbol and timeframe window."""
    from flask import request
    sym = request.args.get("symbol", "").upper()
    window = request.args.get("window", "15m")
    # Map window to Binance interval + limit
    cfg = {
        "1m":  ("1m",  60),
        "15m": ("1m",  900),
        "1h":  ("5m",  720),
        "1d":  ("1h",  24),
        "1w":  ("4h",  42),
        "1M":  ("1d",  30),
    }.get(window, ("1m", 60))
    interval, limit = cfg
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval={interval}&limit={limit}"
        with urllib.request.urlopen(url, timeout=10) as r:
            rows = json.loads(r.read())
        # rows: [openTime, open, high, low, close, ...]
        result = [{"ts": row[0], "price": float(row[4])} for row in rows]
        return jsonify(result)
    except Exception as e:
        return jsonify([])


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
