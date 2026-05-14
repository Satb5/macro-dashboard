"""
Macro Morning Dashboard — Railway deployment version
=====================================================
Local run:   python macro_dashboard.py
Cloud:       Deployed via Railway (see README)

Environment variables to set in Railway dashboard:
    ANTHROPIC_API_KEY   your Anthropic API key (required for AI brief)
"""

import os
import threading
import webbrowser
from datetime import datetime
from flask import Flask, jsonify, render_template_string
import yfinance as yf
import anthropic

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PORT    = int(os.environ.get("PORT", 5000))
DEBUG   = False

SECTORS = [
    ("Financials",     "XLF"),
    ("Energy",         "XLE"),
    ("Technology",     "XLK"),
    ("Industrials",    "XLI"),
    ("Utilities",      "XLU"),
    ("Materials",      "XLB"),
    ("Consumer disc.", "XLY"),
    ("Health care",    "XLV"),
]

MACRO_TICKERS = {
    "SPY":       "S&P 500",
    "QQQ":       "Nasdaq 100",
    "^VIX":      "VIX",
    "^TNX":      "10yr yield",
    "^IRX":      "3m T-bill",
    "GC=F":      "Gold",
    "CL=F":      "WTI Oil",
    "DX-Y.NYB":  "USD Index",
}

# ── Data fetching ─────────────────────────────────────────────────────────────
def fetch_quotes(tickers: list) -> dict:
    results = {}
    try:
        data = yf.download(
            tickers,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        for t in tickers:
            try:
                if len(tickers) == 1:
                    closes = data["Close"].dropna()
                else:
                    closes = data["Close"][t].dropna()
                if len(closes) < 2:
                    continue
                price = float(closes.iloc[-1])
                prev  = float(closes.iloc[-2])
                chg   = ((price - prev) / prev * 100) if prev else 0.0
                results[t] = {"price": round(price, 4), "chg": round(chg, 3)}
            except Exception:
                pass
    except Exception as e:
        print(f"[data] yfinance error: {e}")
    return results


def build_market_snapshot() -> dict:
    all_tickers = list(MACRO_TICKERS.keys()) + [t for _, t in SECTORS]
    quotes      = fetch_quotes(all_tickers)

    macro = {}
    for ticker, label in MACRO_TICKERS.items():
        q = quotes.get(ticker, {})
        macro[label] = {
            "ticker": ticker,
            "price":  q.get("price"),
            "chg":    q.get("chg"),
        }

    sectors = []
    for name, ticker in SECTORS:
        q = quotes.get(ticker, {})
        sectors.append({
            "name":   name,
            "ticker": ticker,
            "price":  q.get("price"),
            "chg":    q.get("chg"),
        })

    t10 = (macro.get("10yr yield") or {}).get("price")
    t3m = (macro.get("3m T-bill")  or {}).get("price")
    oil = (macro.get("WTI Oil")    or {}).get("price")
    usd = (macro.get("USD Index")  or {}).get("chg")
    vix = (macro.get("VIX")        or {}).get("price")

    regime = {}
    if t10 and t3m:
        spread = round(t10 - t3m / 10, 2)
        if spread > 0.75:
            curve_lbl, curve_cls = "Bear steepening", "ok"
        elif spread > 0.25:
            curve_lbl, curve_cls = "Mild steepening", "warn"
        elif spread < -0.5:
            curve_lbl, curve_cls = "Inverted", "bad"
        else:
            curve_lbl, curve_cls = "Flat / transitioning", "warn"
        regime["yield_curve"] = {
            "spread": spread, "label": curve_lbl, "cls": curve_cls,
            "detail": f"10yr {t10:.2f}% vs 3m {t3m/10:.2f}%"
        }
    if oil:
        if oil > 90:
            oil_lbl, oil_cls = "High — stagflation risk", "bad"
        elif oil > 75:
            oil_lbl, oil_cls = "Moderate", "warn"
        else:
            oil_lbl, oil_cls = "Low — disinflationary", "ok"
        regime["oil"] = {"price": oil, "label": oil_lbl, "cls": oil_cls}
    if usd is not None:
        if usd > 0.4:
            usd_lbl = "USD strengthening"
        elif usd < -0.4:
            usd_lbl = "USD weakening"
        else:
            usd_lbl = "USD stable"
        regime["usd"] = {"chg": usd, "label": usd_lbl}
    if vix:
        if vix > 30:
            vix_regime, iv_signal = "Extreme fear", "Spreads better — IV very elevated"
        elif vix > 20:
            vix_regime, iv_signal = "Elevated vol", "Consider spreads to cut cost"
        elif vix > 15:
            vix_regime, iv_signal = "Moderate", "Neutral — assess each trade"
        else:
            vix_regime, iv_signal = "Complacent", "Favourable — options relatively cheap"
        regime["vix"] = {
            "price": vix, "regime": vix_regime, "iv_signal": iv_signal,
            "entry": "Favourable" if vix < 16 else ("Neutral" if vix < 22 else "Expensive")
        }

    return {
        "macro":     macro,
        "sectors":   sectors,
        "regime":    regime,
        "timestamp": datetime.now().strftime("%A %d %B %Y, %I:%M %p AEST"),
    }


# ── AI brief ──────────────────────────────────────────────────────────────────
def generate_brief(snapshot: dict) -> dict:
    if not API_KEY:
        return {
            "text": "Set ANTHROPIC_API_KEY in Railway environment variables to enable the AI brief.",
            "signal": ""
        }

    lines = []
    for label, d in snapshot["macro"].items():
        if d.get("price") is not None:
            chg_str = f" ({'+' if d['chg']>0 else ''}{d['chg']:.2f}%)" if d.get("chg") is not None else ""
            lines.append(f"{label}: {d['price']}{chg_str}")
    for s in snapshot["sectors"]:
        if s.get("chg") is not None:
            lines.append(f"{s['name']} ({s['ticker']}): {'+' if s['chg']>0 else ''}{s['chg']:.2f}%")

    data_block = "\n".join(lines) if lines else "Live data unavailable."
    today      = datetime.now().strftime("%A %d %B %Y")

    system = (
        "You are a macro research analyst writing a morning brief for a swing trader "
        "focused on US equities and options. The trader uses two strategies: "
        "(1) macro overreaction plays — stocks that dropped due to unrelated macro events "
        "expected to mean-revert over 2-7 days; "
        "(2) regime change beneficiaries — sectors positioned to benefit from shifting macro conditions. "
        "Write exactly two prose paragraphs. No bullet points, no headers, no markdown. "
        "First paragraph: interpret today's macro picture and what it signals for equity markets. "
        "Second paragraph: identify one specific actionable angle with a suggested options approach "
        "(direction, rough delta/strike guidance, expiry timeframe). "
        "End your response with exactly one signal label on its own line: "
        "OVERREACTION WATCH / REGIME SHIFT / NO CLEAR SIGNAL. "
        "Keep total response under 230 words."
    )

    try:
        client = anthropic.Anthropic(api_key=API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=450,
            system=system,
            messages=[{
                "role": "user",
                "content": f"Today is {today}. Live market data:\n{data_block}\n\nWrite the morning brief."
            }]
        )
        raw    = msg.content[0].text.strip()
        paras  = [l.strip() for l in raw.split("\n") if l.strip()]
        last   = paras[-1] if paras else ""
        is_sig = any(k in last for k in ("WATCH", "SHIFT", "NO CLEAR"))
        body   = "\n\n".join(paras[:-1]) if is_sig else "\n\n".join(paras)
        signal = last if is_sig else ""
        return {"text": body, "signal": signal}
    except Exception as e:
        return {"text": f"Brief generation error: {e}", "signal": ""}


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Macro Morning Dashboard</title>
<style>
  :root {
    --bg:#0f0f0f; --surface:#1a1a1a; --border:#2a2a2a;
    --text:#e8e8e8; --muted:#888; --dim:#555;
    --up:#4ade80; --down:#f87171; --warn:#fbbf24; --info:#60a5fa;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'SF Pro Text','Segoe UI',system-ui,sans-serif;font-size:14px;line-height:1.6;}
  .wrap{max-width:960px;margin:0 auto;padding:2rem 1.5rem;}
  .header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:2rem;gap:1rem;flex-wrap:wrap;}
  h1{font-size:22px;font-weight:600;letter-spacing:-0.3px;}
  .ts{font-size:12px;color:var(--muted);margin-top:4px;}
  button{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px 16px;cursor:pointer;font-size:13px;display:flex;align-items:center;gap:6px;transition:background 0.15s;}
  button:hover{background:#222;}
  button:disabled{opacity:0.4;cursor:not-allowed;}
  .section-lbl{font-size:11px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:var(--dim);margin:2rem 0 0.75rem;}
  .grid-5{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;}
  .grid-3{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;}
  .grid-8{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}
  .card-lbl{font-size:11px;color:var(--muted);margin-bottom:6px;}
  .card-val{font-size:20px;font-weight:600;}
  .card-sub{font-size:12px;color:var(--muted);margin-top:4px;}
  .regime-row{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 16px;display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap;}
  .regime-lbl{font-size:12px;color:var(--muted);min-width:90px;}
  .regime-val{font-size:14px;font-weight:500;flex:1;}
  .badge{font-size:11px;font-weight:600;padding:4px 10px;border-radius:6px;white-space:nowrap;}
  .badge-ok{background:#14532d;color:#4ade80;}
  .badge-warn{background:#451a03;color:#fbbf24;}
  .badge-bad{background:#450a0a;color:#f87171;}
  .badge-info{background:#1e3a5f;color:#60a5fa;}
  .sector-row{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;}
  .sector-name{font-size:13px;}
  .sector-tick{font-size:11px;color:var(--muted);}
  .sector-chg{font-size:14px;font-weight:600;}
  .brief-box{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.5rem;}
  .brief-text{font-size:14px;line-height:1.8;color:#ccc;}
  .brief-text p{margin-bottom:0.75rem;}
  .brief-text p:last-child{margin-bottom:0;}
  .pill-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:1rem;padding-top:1rem;border-top:1px solid var(--border);}
  .pill{font-size:11px;font-weight:600;padding:5px 12px;border-radius:6px;cursor:pointer;border:none;}
  .up{color:var(--up);}
  .down{color:var(--down);}
  .flat{color:var(--muted);}
  .spin{animation:spin 1s linear infinite;display:inline-block;}
  @keyframes spin{to{transform:rotate(360deg)}}
  .footer{font-size:11px;color:var(--dim);margin-top:2rem;padding-top:1rem;border-top:1px solid var(--border);}
  .sk{background:var(--border);border-radius:6px;animation:pulse 1.5s ease-in-out infinite;}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div>
      <h1>&#9643; Morning macro brief</h1>
      <p class="ts" id="ts">Loading...</p>
    </div>
    <button id="rbtn" onclick="refresh()">&#8635; Refresh all</button>
  </div>

  <div class="section-lbl">Macro regime</div>
  <div id="regime"></div>

  <div class="section-lbl">Key indicators</div>
  <div class="grid-5" id="metrics"></div>

  <div class="section-lbl">Volatility &amp; options conditions</div>
  <div class="grid-3" id="vol"></div>

  <div class="section-lbl">Sector ETF performance (1-day)</div>
  <div class="grid-8" id="sectors"></div>

  <div class="section-lbl">AI macro brief</div>
  <div class="brief-box" id="brief">
    <div class="sk" style="height:13px;width:90%;margin-bottom:8px"></div>
    <div class="sk" style="height:13px;width:75%;margin-bottom:8px"></div>
    <div class="sk" style="height:13px;width:85%"></div>
  </div>

  <p class="footer" id="footer">Data via yfinance &bull; Brief by Claude Sonnet &bull; Prices 15-min delayed</p>
</div>
<script>
function fmt(n,d=2){return(n==null||isNaN(n))?'&#8212;':Number(n).toFixed(d);}
function sign(n){return(n>0)?'+':'';}
function cc(n){return(n==null)?'flat':(n>0?'up':'down');}

async function refresh(){
  const btn=document.getElementById('rbtn');
  btn.disabled=true;
  btn.innerHTML='<span class="spin">&#8635;</span> Refreshing...';
  document.getElementById('ts').textContent='Fetching live data...';
  try{
    const r=await fetch('/api/snapshot');
    const d=await r.json();
    renderAll(d);
    document.getElementById('ts').textContent=d.timestamp||'';
    document.getElementById('footer').textContent=
      'Last refreshed '+new Date().toLocaleTimeString('en-AU',{hour:'2-digit',minute:'2-digit'})+
      ' \u2022 Data via yfinance \u2022 Brief by Claude Sonnet';
  }catch(e){
    document.getElementById('ts').textContent='Error loading data';
  }
  btn.disabled=false;
  btn.innerHTML='&#8635; Refresh all';
}

function renderAll(d){renderRegime(d.regime);renderMetrics(d.macro);renderVol(d.regime);renderSectors(d.sectors);renderBrief(d.brief);}

function renderRegime(r){
  let html='';
  if(r.yield_curve){const yc=r.yield_curve;html+=`<div class="regime-row"><span class="regime-lbl">Yield curve</span><span class="regime-val">${yc.detail}&nbsp;<small style="color:#888">spread ${yc.spread>=0?'+':''}${yc.spread}%</small></span><span class="badge badge-${yc.cls}">${yc.label}</span></div>`;}
  if(r.oil){html+=`<div class="regime-row"><span class="regime-lbl">WTI oil</span><span class="regime-val">$${fmt(r.oil.price,1)}/bbl</span><span class="badge badge-${r.oil.cls}">${r.oil.label}</span></div>`;}
  if(r.usd){html+=`<div class="regime-row"><span class="regime-lbl">USD index</span><span class="regime-val"><span class="${cc(r.usd.chg)}">${sign(r.usd.chg)}${fmt(r.usd.chg)}% today</span></span><span class="badge badge-info">${r.usd.label}</span></div>`;}
  document.getElementById('regime').innerHTML=html||'<p style="color:#888;font-size:13px">No regime data</p>';
}

function renderMetrics(m){
  const items=[
    {l:'S&P 500',k:'S&P 500',pre:'$',dec:2},
    {l:'Nasdaq 100',k:'Nasdaq 100',pre:'$',dec:2},
    {l:'10yr yield',k:'10yr yield',suf:'%',dec:3},
    {l:'Gold',k:'Gold',pre:'$',dec:0},
    {l:'Oil (WTI)',k:'WTI Oil',pre:'$',dec:2},
  ];
  document.getElementById('metrics').innerHTML=items.map(i=>{
    const d=m[i.k]||{};
    const val=d.price!=null?(i.pre||'')+fmt(d.price,i.dec||2)+(i.suf||''):'&#8212;';
    const sub=d.chg!=null?`<span class="${cc(d.chg)}">${sign(d.chg)}${fmt(d.chg)}%</span> today`:'';
    return `<div class="card"><div class="card-lbl">${i.l}</div><div class="card-val">${val}</div><div class="card-sub">${sub}</div></div>`;
  }).join('');
}

function renderVol(r){
  const v=r.vix||{};
  const cards=[
    {l:'VIX',val:v.price?fmt(v.price,1):'&#8212;',sub:v.regime||'&#8212;'},
    {l:'Vol regime',val:v.regime||'&#8212;',sub:v.iv_signal||'&#8212;'},
    {l:'Options entry',val:v.entry||'&#8212;',sub:v.entry==='Favourable'?'Low IV \u2014 good to buy options':v.entry==='Expensive'?'Consider spreads to reduce cost':'Assess each trade individually'},
  ];
  document.getElementById('vol').innerHTML=cards.map(c=>
    `<div class="card"><div class="card-lbl">${c.l}</div><div class="card-val">${c.val}</div><div class="card-sub">${c.sub}</div></div>`
  ).join('');
}

function renderSectors(sectors){
  document.getElementById('sectors').innerHTML=sectors.map(s=>{
    const chg=s.chg;
    return `<div class="sector-row"><div><div class="sector-name">${s.name}</div><div class="sector-tick">${s.ticker}</div></div><div class="sector-chg ${cc(chg)}">${chg!=null?sign(chg)+fmt(chg)+'%':'&#8212;'}</div></div>`;
  }).join('');
}

function renderBrief(b){
  if(!b||!b.text){document.getElementById('brief').innerHTML='<p style="color:#888;font-size:13px">No brief \u2014 set ANTHROPIC_API_KEY in Railway environment variables</p>';return;}
  const paras=b.text.split('\\n\\n').filter(p=>p.trim()).map(p=>`<p>${p.trim()}</p>`).join('');
  let pills='';
  if(b.signal){const cls=b.signal.includes('OVERREACTION')?'badge-warn':b.signal.includes('REGIME')?'badge-ok':'badge-info';pills+=`<span class="pill badge ${cls}">${b.signal}</span>`;}
  pills+=`<button class="pill badge-info" onclick="window.open('https://claude.ai','_blank')">Open Claude to dig deeper &rarr;</button>`;
  document.getElementById('brief').innerHTML=`<div class="brief-text">${paras}</div><div class="pill-row">${pills}</div>`;
}

refresh();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/snapshot")
def api_snapshot():
    snapshot = build_market_snapshot()
    snapshot["brief"] = generate_brief(snapshot)
    return jsonify(snapshot)


if __name__ == "__main__":
    is_local = os.environ.get("RAILWAY_ENVIRONMENT") is None
    if is_local:
        url = f"http://localhost:{PORT}"
        print(f"\n  Macro Dashboard running at {url}\n")
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
