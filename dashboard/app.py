import json, os
from pathlib import Path
from flask import Flask, render_template_string

app = Flask(__name__)
BASE = Path(__file__).parent.parent / "data"

def load_json(name):
    try:
        return json.loads((BASE / name).read_text())
    except Exception:
        return None

def load_jsonl(name, last=20):
    try:
        lines = (BASE / name).read_text().strip().splitlines()
        return [json.loads(l) for l in lines[-last:]]
    except Exception:
        return None

TEMPLATE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Crypto Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0f1117;color:#e0e0e0;padding:20px}
  h1{font-size:1.4rem;margin-bottom:16px;color:#fff}
  .cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}
  .card{background:#1a1d27;border-radius:8px;padding:16px 24px;min-width:140px;flex:1}
  .card .label{font-size:.7rem;text-transform:uppercase;color:#888;margin-bottom:4px}
  .card .value{font-size:1.6rem;font-weight:700}
  .pos{color:#26c17c} .neg{color:#e05252} .neu{color:#7eb6ff}
  .chart-box{background:#1a1d27;border-radius:8px;padding:16px;margin-bottom:24px}
  .chart-box h2{font-size:.85rem;color:#aaa;margin-bottom:12px;text-transform:uppercase}
  table{width:100%;border-collapse:collapse;background:#1a1d27;border-radius:8px;overflow:hidden}
  th{background:#12151e;font-size:.7rem;text-transform:uppercase;color:#888;padding:8px 12px;text-align:left}
  td{padding:7px 12px;font-size:.8rem;border-top:1px solid #252836}
  tr:hover td{background:#20243a}
  .no-data{color:#888;font-style:italic;padding:20px 0}
</style>
</head><body>
<h1>Crypto Paper Trading — Dashboard</h1>

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

<h2 style="font-size:.85rem;text-transform:uppercase;color:#aaa;margin-bottom:10px">
  Open Positions</h2>
{% if open_positions %}
<table style="margin-bottom:24px">
  <thead><tr>
    <th>Pair</th><th>Direction</th>
    <th>Entry Z</th><th>Current Z</th>
    <th>Entry Price A</th><th>Current Price A</th><th>Leg A PnL</th>
    <th>Entry Price B</th><th>Current Price B</th><th>Leg B PnL</th>
    <th>Notional</th><th>Leverage</th><th>Total uPnL</th>
    <th>Stop Loss</th><th>Exit Target</th>
    <th>Opened</th><th>Last Updated</th>
  </tr></thead>
  <tbody>
  {% for p in open_positions %}
  {% set sl_pnl = -(p.get('notional_a',0) + p.get('notional_b',0)) * 0.15 %}
  <tr>
    <td>{{ p.get('pair_key','—') }}</td>
    <td>{{ p.get('direction','—') }}</td>
    <td>{{ "%.2f"|format(p.get('entry_zscore',0)) }}</td>
    <td>{{ "%.2f"|format(p.get('current_zscore',0)) if p.get('current_zscore') is not none else '—' }}</td>
    <td>${{ "%.4f"|format(p.get('entry_price_a',0)) }}</td>
    <td>{{ "$%.4f"|format(p.get('current_price_a',0)) if p.get('current_price_a') is not none else '—' }}</td>
    <td class="{{ 'pos' if p.get('pnl_a',0)>=0 else 'neg' }}">{{ "$%.2f"|format(p.get('pnl_a',0)) if p.get('pnl_a') is not none else '—' }}</td>
    <td>${{ "%.4f"|format(p.get('entry_price_b',0)) }}</td>
    <td>{{ "$%.4f"|format(p.get('current_price_b',0)) if p.get('current_price_b') is not none else '—' }}</td>
    <td class="{{ 'pos' if p.get('pnl_b',0)>=0 else 'neg' }}">{{ "$%.2f"|format(p.get('pnl_b',0)) if p.get('pnl_b') is not none else '—' }}</td>
    <td>${{ "%.2f"|format(p.get('notional_a',0)) }}</td>
    <td class="neu">{{ leverage }}x</td>
    <td class="{{ 'pos' if p.get('pnl',0)>=0 else 'neg' }}">${{ "%.2f"|format(p.get('pnl',0)) }}</td>
    <td class="neg">${{ "%.2f"|format(sl_pnl) }}</td>
    <td class="neu">z → 0.0</td>
    <td>{{ p.get('entry_time','—')[:19] }}</td>
    <td>{{ p.get('last_updated','—')[:19] if p.get('last_updated') else '—' }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="no-data" style="margin-bottom:24px">No open positions.</p>
{% endif %}

<h2 style="font-size:.85rem;text-transform:uppercase;color:#aaa;margin-bottom:10px">
  Recent Trades (last 20)</h2>
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
</body></html>"""

@app.route("/")
def index():
    state = load_json("state.json")
    equity_data = load_json("equity_curve.json")

    if state:
        closed = (state.get("closed_positions") or [])[-20:]
        trades = closed
        open_positions = state.get("open_positions") or []
        # Build summary from state fields directly
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
        leverage=LEVERAGE,
        equity_labels=equity_labels,
        equity_values=equity_values,
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
