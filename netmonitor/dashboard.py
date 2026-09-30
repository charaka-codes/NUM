"""
dashboard.py — self-contained interactive panel (nested Liquid Glass cards).

Structure (main view):
  • current network + live speeds      (card)
  • Day / Year tabs + calendar cells   (card)
  • big total for the selected period  (card)
  • per-network breakdown (black nums) (card)
  • Settings + Quit buttons
  • generated line

Settings view (gear): tracking toggle, show-menu-text toggle, transparency
slider, download/upload color pickers, export, open data folder, about line.

All data + settings are embedded as JSON; JS handles views, tabs, navigation,
selection, and settings. Native actions go through window.webkit bridge.
"""

import json
from datetime import datetime

from . import core
from . import settings as settings_mod

VERSION = "2.5.0"
AUTHOR = "Charaka (@charaka-codes)"


def _gather(con):
    """Per-day totals from the month rollups (exclusions applied)."""
    ex = core.excluded_fingerprints()
    out = {}
    for day, net, down, up in core._store().daily_rows():
        if net in ex:
            continue
        cur = out.get(day, [0, 0])
        cur[0] += down or 0
        cur[1] += up or 0
        out[day] = cur
    return out


def _gather_networks(con):
    rows = core._store().daily_rows()
    # Resolve fingerprint/label keys to display names and re-aggregate, so the
    # very first render never shows a raw 'gw:..'/'ip:..' key (which used to
    # flash before the first refresh corrected it).
    byday = {}
    for d, net, down, up in rows:
        byday.setdefault(d, []).append((net, down, up))
    out = {}
    for d, day_rows in byday.items():
        resolved = core._resolve_rows(day_rows)
        out[d] = [[n, dn, up] for (n, dn, up) in resolved]
    return out


def _networks_with_totals(con):
    """Known networks for the Networks settings screen, each with its all-time
    total so the user can identify which is which. Returns list of
    {fp, display, custom, hasName, down, up}."""
    try:
        rows = core.all_breakdown()
    except Exception:
        rows = []
    # Sum per display fingerprint/key.
    totals = {}
    for netkey, d, u in rows:
        cur = totals.get(netkey, (0, 0))
        totals[netkey] = (cur[0] + (d or 0), cur[1] + (u or 0))
    out = []
    known = {n["fp"]: n for n in core.known_networks()}
    # Include every known network (even zero-usage), plus any raw keys with data.
    seen = set()
    for fp, n in known.items():
        d, u = totals.get(fp, (0, 0))
        out.append({"fp": fp, "display": n["display"], "custom": n["custom"],
                    "down": d, "up": u, "excluded": n.get("excluded", False)})
        seen.add(fp)
    for netkey, (d, u) in totals.items():
        if netkey in seen:
            continue
        # Legacy label rows (not fingerprints) — show but not renamable here.
        if not (netkey.startswith("gw:") or netkey.startswith("ip:")):
            out.append({"fp": netkey, "display": core.display_network(netkey),
                        "custom": None, "down": d, "up": u, "legacy": True})
    out.sort(key=lambda r: (r["down"] + r["up"]), reverse=True)
    return out


def build_html(con, month=None):
    now = datetime.now()
    down_bps, up_bps = core.recent_speed(con, seconds=8)
    iface = core.active_interface()
    net = core.network_label(iface, allow_compute=False) if iface else "offline"
    st = settings_mod.load()
    data = {
        "daily": _gather(con),
        "networks": _gather_networks(con),
        "today": now.strftime("%Y-%m-%d"),
        "net": net,
        "downBps": down_bps,
        "upBps": up_bps,
        "generated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": st,
        "version": VERSION,
        "author": AUTHOR,
        "months": core.months_available(con),
        "years": core.years_available(con),
        "knownNetworks": _networks_with_totals(con),
    }
    # Include per-app data up front so the Apps tab has it on first open.
    try:
        from . import apps as apps_mod
        data["appData"] = apps_mod.app_dashboard_payload(
            con, "month", now.strftime("%Y-%m"), top=25)
    except Exception:
        data["appData"] = {"period": "month", "value": now.strftime("%Y-%m"), "apps": []}
    return _TEMPLATE.replace("__DATA__", json.dumps(data))


def write_dashboard(con, path, month=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html(con, month))
    return path


_TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NUM</title>
<style>
  :root{
    --panel-alpha: 0.42;
    --card-alpha: 0.72;
    --panel: rgba(230,231,235,var(--panel-alpha));
    --card: rgba(255,255,255,var(--card-alpha));
    --card-border: rgba(0,0,0,0.08);
    --txt: #1d1d1f;
    --txt-dim: rgba(0,0,0,0.55);
    --txt-secondary: var(--txt-dim);
    --txt-faint: rgba(0,0,0,0.4);
    --line: rgba(0,0,0,0.08);
    --glass-border: rgba(0,0,0,0.15);
    --glass-border-soft: var(--line);
    --glass-strong: rgba(255,255,255,0.85);
    --solid-surface: #fff;
    --input-bg: #fff;
    --knob: #fff;
    --track-off: #dcdcdc;
    --tile-empty: rgba(255,255,255,0.4);
    --dn: #2e9e5b;
    --up: #e0603f;
    --sel-border: rgba(0,0,0,0.55);
    --blue: #3478f6;
  }
  /* Dark palette — either the OS is in dark mode and the user hasn't forced
     light, or the user explicitly picked dark regardless of the OS. --card/
     --card-alpha/--dn/--up are deliberately NOT redefined here: they're set
     live by applyColors() in JS (which is itself theme-aware, see below), so
     a value here would just be overwritten on the next render anyway. */
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --panel: rgba(28,28,30,var(--panel-alpha));
      --card-border: rgba(255,255,255,0.12);
      --txt: #f5f5f7;
      --txt-dim: rgba(255,255,255,0.6);
      --txt-faint: rgba(255,255,255,0.38);
      --line: rgba(255,255,255,0.12);
      --glass-border: rgba(255,255,255,0.18);
      --glass-strong: rgba(255,255,255,0.14);
      --solid-surface: rgba(255,255,255,0.20);
      --input-bg: #232324;
      --knob: #fff;
      --track-off: #48484a;
      --tile-empty: rgba(255,255,255,0.06);
      --sel-border: rgba(255,255,255,0.55);
      --blue: #0a84ff;
    }
  }
  :root[data-theme="dark"] {
    --panel: rgba(28,28,30,var(--panel-alpha));
    --card-border: rgba(255,255,255,0.12);
    --txt: #f5f5f7;
    --txt-dim: rgba(255,255,255,0.6);
    --txt-faint: rgba(255,255,255,0.38);
    --line: rgba(255,255,255,0.12);
    --glass-border: rgba(255,255,255,0.18);
    --glass-strong: rgba(255,255,255,0.14);
    --solid-surface: rgba(255,255,255,0.20);
    --input-bg: #232324;
    --knob: #fff;
    --track-off: #48484a;
    --tile-empty: rgba(255,255,255,0.06);
    --sel-border: rgba(255,255,255,0.55);
    --blue: #0a84ff;
  }
  html{color-scheme: light dark;}
  *{box-sizing:border-box; margin:0; padding:0;}
  html,body{background:transparent;}
  body{font-family:-apple-system,"SF Pro Text",Helvetica,Arial,sans-serif;
    padding:10px; color:var(--txt); -webkit-font-smoothing:antialiased;}
  .panel{max-width:380px; margin:0 auto;}
  .outer{background:transparent; border:none; border-radius:0; padding:2px;
    display:flex; flex-direction:column; gap:9px;}
  .card{background:var(--card); border:none;
    border-radius:14px; padding:12px;}
  .cap{font-size:10px; text-transform:uppercase; letter-spacing:.4px;
    color:var(--txt-dim); margin-bottom:8px;}

  .speeds{display:flex; gap:12px;}
  .speed{flex:1;}
  .speed .big{font-size:20px; font-weight:500;}
  .speed .big small{font-size:11px; color:var(--txt-dim);}
  .speed .lbl{font-size:11px; color:var(--txt-dim); margin-top:2px;}
  .dot{height:8px;width:8px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle;}

  .tabs{display:flex; background:rgba(120,120,120,0.16); border-radius:9px;
    padding:3px; margin-bottom:10px;}
  .tabs button{flex:1; border:none; background:transparent; font-size:12px; padding:6px;
    border-radius:7px; cursor:pointer; color:var(--txt-dim); font-weight:500;}
  .tabs button.active{background:var(--solid-surface); color:var(--txt);}
  .nav{display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;}
  .nav button{border:none; background:transparent; font-size:18px; color:var(--txt-dim);
    cursor:pointer; padding:0 8px; line-height:1;}
  .nav .lbl{font-size:13px; font-weight:500;}

  .grid7{display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:3px;}
  .grid7 .dow{text-align:center; font-size:9px; color:var(--txt-faint);}
  .cell{min-width:0; height:60px; border-radius:8px; padding:4px 3px; display:flex; flex-direction:column;
    gap:1px; border:0.5px solid var(--card-border);
    background:var(--card); cursor:pointer; overflow:hidden;}
  .cell .dn-n{font-size:10px; color:var(--txt);}
  .cell .v{line-height:1.25;}
  .cell .v .d{font-size:9px; color:var(--dn);}
  .cell .v .u{font-size:9px; color:var(--up);}
  .cell.empty{cursor:default; background:var(--tile-empty);}
  .cell.empty .dn-n{color:var(--txt-faint);}
  .cell.sel{background:var(--solid-surface); border:1.5px solid var(--sel-border);}
  .cell.sel .dn-n{font-weight:600;}

  .bigcard{text-align:center;}
  .bigcard .when{font-size:10px; color:var(--txt-dim); text-transform:uppercase; margin-bottom:4px;}
  .bigcard .tot{font-size:28px; font-weight:500;}
  .bigcard .sub{font-size:13px; margin-top:3px;}
  .bigcard .sub .d{color:var(--dn);} .bigcard .sub .u{color:var(--up);}

  .nethead{display:flex; font-size:10px; text-transform:uppercase; letter-spacing:.3px;
    color:var(--txt-faint); padding-bottom:5px; border-bottom:0.5px solid var(--line);}
  .nethead .nm{flex:1;} .nethead .d,.nethead .u{width:74px; text-align:right; white-space:nowrap;}
  .netrow{display:flex; font-size:12px; padding:6px 0; border-bottom:0.5px solid rgba(0,0,0,0.05);}
  .netrow:last-child{border-bottom:none;}
  .netrow .nm{flex:1; color:var(--txt); min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .netrow .d,.netrow .u{width:74px; text-align:right; color:var(--txt); white-space:nowrap;}

  .months{display:grid; grid-template-columns:repeat(3,1fr); gap:5px;}
  .mtile{border-radius:9px; padding:6px 7px; border:0.5px solid var(--card-border);
    background:var(--card); cursor:pointer;}
  .mtile .mn{font-size:11px; color:var(--txt);}
  .mtile .mv{line-height:1.3; margin-top:3px;}
  .mtile .mv .d{font-size:9.5px; color:var(--dn);} .mtile .mv .u{font-size:9.5px; color:var(--up);}
  .mtile.empty{cursor:default; background:var(--tile-empty);}
  .mtile.empty .mn{color:var(--txt-faint);} .mtile.empty .mv{color:var(--txt-faint); font-size:9.5px;}
  .mtile.cur{background:var(--solid-surface); border:1.5px solid var(--sel-border);}
  .mtile.cur .mn{font-weight:600;}
  .totbar{display:flex; justify-content:space-between; font-size:12px; margin-top:10px;
    padding-top:8px; border-top:0.5px solid var(--line);}
  .totbar .d{color:var(--dn);} .totbar .u{color:var(--up);}

  .btnrow{display:flex; gap:10px;}
  .btn{flex:1; padding:10px; border-radius:12px; background:var(--card);
    border:0.5px solid rgba(0,0,0,0.12); color:var(--txt); font-size:13px;
    display:flex; align-items:center; justify-content:center; gap:6px; cursor:pointer;
    font-family:inherit;}
  .btn:hover{background:var(--solid-surface);}
  .btn.quit{color:#c0402a;}
  .ic{font-size:15px; line-height:1;}
  .foot{text-align:center; color:var(--txt-faint); font-size:10px; margin-top:8px; padding-bottom:2px;}

  /* settings */
  .shead{display:flex; align-items:center; gap:8px; margin-bottom:4px;}
  .shead .back{cursor:pointer; font-size:18px; color:var(--txt-dim);}
  .shead .ttl{font-size:15px; font-weight:500;}
  .srow{display:flex; align-items:center; justify-content:space-between; padding:9px 0;
    border-bottom:0.5px solid var(--line); font-size:13px;}
  .srow:last-child{border-bottom:none;}
  .sw{width:36px; height:16px; border-radius:8px; background:var(--track-off);
    position:relative; cursor:pointer; transition:background .15s;}
  .sw .kn{position:absolute; top:1.7px; left:1.6px; width:21px; height:12.6px;
    border-radius:6.3px; background:var(--knob); transition:left .15s; box-shadow:0 1px 2px rgba(0,0,0,.18);}
  .sw.on{background:#0076f5;}
  .sw.on .kn{left:13.4px;}
  .seg{display:flex; background:rgba(120,120,120,0.14); border:0.5px solid rgba(120,120,120,0.25); border-radius:8px; padding:2px;}
  .segopt{font-size:11px; padding:4px 9px; border-radius:6px; cursor:pointer; color:var(--txt-secondary); white-space:nowrap;}
  .segopt.on{background:var(--solid-surface); border:0.5px solid rgba(120,120,120,0.3); color:var(--txt);}
  .swatches{display:flex; gap:6px;}
  .swatch{width:18px;height:18px;border-radius:50%;cursor:pointer;}
  .swatch.sel{outline:2px solid rgba(0,0,0,0.4); outline-offset:1px;}
  .cpick{display:inline-block; width:26px; height:26px; border-radius:50%;
    cursor:pointer; border:2px solid rgba(255,255,255,0.8);
    box-shadow:0 0 0 0.5px rgba(0,0,0,0.2); overflow:hidden; position:relative;}
  .cpick input[type=color]{position:absolute; top:-6px; left:-6px;
    width:40px; height:40px; border:none; padding:0; background:transparent; cursor:pointer;}
  .sitem{display:flex; align-items:center; gap:8px; padding:10px 0; cursor:pointer;
    font-size:13px; border-bottom:0.5px solid var(--line);}
  .sitem:last-child{border-bottom:none;}
  .sitem i{font-size:17px; color:var(--txt-dim);}
  input[type=range]{width:110px;}
  .exptabs{display:flex; gap:5px; background:rgba(120,120,120,0.14); border-radius:9px;
    padding:3px; margin-bottom:10px;}
  .etab{flex:1; text-align:center; font-size:12px; padding:6px; border-radius:7px;
    cursor:pointer; color:var(--txt-dim);}
  .etab.on{background:var(--solid-surface); color:var(--txt); border:0.5px solid var(--sel-border);}
  .expsel{width:100%; font-size:13px; padding:7px 8px; border-radius:8px;
    border:0.5px solid rgba(0,0,0,0.2); background:var(--input-bg); color:var(--txt); margin-bottom:10px;}
  .expall{font-size:12px; color:var(--txt-dim); padding:6px 2px 12px;}
  .expbtn{width:100%; padding:9px; border-radius:9px; background:rgba(46,158,91,0.14);
    border:0.5px solid rgba(46,158,91,0.3); color:#1d7a45; font-size:13px; cursor:pointer;
    font-family:inherit;}
  .expbtn:hover{background:rgba(46,158,91,0.22);}
</style></head>
<body><div class="panel">
  <div class="outer" id="root"></div>
  <div class="foot" id="foot"></div>
</div>

<script>
const DATA = __DATA__;
const DOW=["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const MON=["January","February","March","April","May","June","July","August",
  "September","October","November","December"];
const MONS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DN_COLORS=["#2e9e5b","#3478f6","#8a63d2","#1d9e75"];
const UP_COLORS=["#e0603f","#d4547e","#e0a020","#c0402a"];

function human(n){ n=Number(n)||0; const u=["B","KB","MB","GB","TB"]; let i=0;
  while(n>=1024&&i<u.length-1){n/=1024;i++;} return (i===0?Math.round(n):n.toFixed(2))+" "+u[i]; }
function short(n){ n=Number(n)||0; const u=["B","K","M","G","T"]; let i=0;
  while(n>=1024&&i<u.length-1){n/=1024;i++;} return (n<10&&i>0?n.toFixed(1):Math.round(n))+u[i]; }
function speed(bps){
  let k=bps/1024;
  if(k<1024) return k.toFixed(1)+" KB/s";
  k/=1024;
  if(k<1024) return k.toFixed(2)+" MB/s";
  k/=1024;
  return k.toFixed(2)+" GB/s";
}
function dkey(d){ return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); }
function get(k){ return DATA.daily[k]||[0,0]; }

let tab="day";
let cur=new Date(DATA.today+"T12:00:00");
let sel=DATA.today;
let yr=new Date(DATA.today+"T12:00:00").getFullYear();
let view="main";  // "main" | "settings"

function isDarkActive(){
  const t=(DATA.settings&&DATA.settings.theme)||"system";
  if(t==="dark") return true;
  if(t==="light") return false;
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

function applyTheme(){
  const t=(DATA.settings&&DATA.settings.theme)||"system";
  const root=document.documentElement;
  if(t==="light") root.setAttribute("data-theme","light");
  else if(t==="dark") root.setAttribute("data-theme","dark");
  else root.removeAttribute("data-theme");
}

function applyColors(){
  const s=DATA.settings;
  const dark=isDarkActive();
  const dn=dark ? (s.down_color_dark||'#34c759') : (s.down_color_light||'#248a3d');
  const up=dark ? (s.up_color_dark||'#ffd60a') : (s.up_color_light||'#e0342a');
  document.documentElement.style.setProperty('--dn', dn);
  document.documentElement.style.setProperty('--up', up);
  // The outer frosted background is the native vibrancy layer. The slider
  // controls how solid the CARDS are on top of it: 0 = cards nearly clear
  // (wallpaper/frost shows through), 100 = cards solid.
  const t=Math.max(0,Math.min(100,s.transparency==null?15:s.transparency))/100;
  // A near-solid WHITE card at the top of the light-mode range would look
  // like a light-mode card mistakenly left on a dark background, so the
  // whole range is subtler in dark mode rather than swapping to a black
  // card — a thin white wash over the dark vibrancy blur is what real
  // system dark popovers use for card separation. Floors lowered from the
  // original 0.08/0.25 — those still left visible white cards even at the
  // slider's minimum, which wasn't actually as transparent as it looked
  // like it should be.
  const cardA=(dark ? (0.03+0.37*t) : (0.06+0.85*t)).toFixed(3);
  document.documentElement.style.setProperty('--card-alpha', cardA);
  document.documentElement.style.setProperty('--card', 'rgba(255,255,255,'+cardA+')');
  const swatches={
    'col-dn-light': s.down_color_light, 'col-dn-dark': s.down_color_dark,
    'col-up-light': s.up_color_light, 'col-up-dark': s.up_color_dark
  };
  for(const id in swatches){
    const el=document.getElementById(id);
    if(el && swatches[id]) el.style.background=swatches[id];
  }
}

function bridge(name){
  try{ if(window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.action){
    window.webkit.messageHandlers.action.postMessage(name); return true; } }catch(e){}
  return false;
}
function requestResize(){ bridge("resize"); }

function weekRange(anchor){
  const s=new Date(anchor); s.setDate(anchor.getDate()-anchor.getDay());
  const e=new Date(s); e.setDate(s.getDate()+6); return [s,e];
}
function netTableHTML(list){
  let h='<div class="nethead"><span class="nm">Network</span><span class="d">↓ Down</span><span class="u">↑ Up</span></div>';
  if(!list||!list.length) return h+'<div class="netrow"><span class="nm">No data</span></div>';
  for(const [nm,d,u] of list)
    h+='<div class="netrow"><span class="nm">'+nm+'</span><span class="d">'+human(d)+'</span><span class="u">'+human(u)+'</span></div>';
  return h;
}

function renderMain(){
  const s=DATA.settings;
  let h='';
  // speeds card
  h+='<div class="card"><div class="cap">Network — '+DATA.net+'</div><div class="speeds">'
    +'<div class="speed"><div class="big">'+speed(DATA.downBps).replace(/ (\w+\/s)/,' <small>$1</small>')+'</div>'
    +'<div class="lbl"><span class="dot" style="background:var(--dn)"></span>Download</div></div>'
    +'<div class="speed"><div class="big">'+speed(DATA.upBps).replace(/ (\w+\/s)/,' <small>$1</small>')+'</div>'
    +'<div class="lbl"><span class="dot" style="background:var(--up)"></span>Upload</div></div></div></div>';

  // calendar / year card
  h+='<div class="card">';
  h+='<div class="tabs"><button data-t="day" class="'+(tab==="day"?"active":"")+'">Day</button>'
    +'<button data-t="year" class="'+(tab==="year"?"active":"")+'">Month</button>'
    +'<button data-t="apps" class="'+(tab==="apps"?"active":"")+'">Apps</button>'
    +'<button data-t="alltime" class="'+(tab==="alltime"?"active":"")+'">All-time</button></div>';
  const navHidden = (tab==="alltime") ? 'style="visibility:hidden"' : '';
  h+='<div class="nav"><button id="prev" '+navHidden+'>&lsaquo;</button>'
    +'<div class="lbl" id="navlabel"></div><button id="next" '+navHidden+'>&rsaquo;</button></div>';
  h+='<div id="calbody"></div></div>';

  // big total card
  h+='<div class="card bigcard" id="bigcard"></div>';

  // network list card
  h+='<div class="card" id="netcard"></div>';

  // buttons
  h+='<div class="btnrow"><button class="btn" id="gear"><span class="ic">⚙</span>Settings</button>'
    +'<button class="btn quit" id="quit"><span class="ic">⏻</span>Quit</button></div>';

  document.getElementById("root").innerHTML=h;
  document.getElementById("foot").innerHTML="Generated "+DATA.generated+"<br><span style=\"opacity:.7\">NUM v"+DATA.version+" · by "+DATA.author+"</span>";

  document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{tab=b.dataset.t; renderMain(); requestResize();});
  document.getElementById("prev").onclick=()=>step(-1);
  document.getElementById("next").onclick=()=>step(1);
  document.getElementById("gear").onclick=()=>{view="settings"; render(); requestResize();};
  document.getElementById("quit").onclick=()=>bridge("quit");

  if(tab==="day") renderDay(); else if(tab==="year") renderYear();
  else if(tab==="alltime") renderAllTime(); else renderApps();
}

// Per-app month anchor (YYYY-MM). Defaults to current month.
let appMonth = (DATA.today||"").slice(0,7);
let appExpanded = {};      // which app rows are expanded
// Month view: which month is drilled into (1-12), or null for the whole year.
// Defaults to the CURRENT month — opening on the year total was misleading,
// since the number people expect on open is "this month so far".
let selMonth = (function(){ try{ return new Date(DATA.today+"T12:00:00").getMonth()+1; }catch(e){ return null; } })();
let appCache = {};         // month -> payload. Sealed months are kept forever.
let appPending = null;     // month we have asked native for
// #netcard is destroyed and rebuilt on every ~1s live tick (renderMain()
// replaces #root wholesale), so the DOM node's own scrollTop can't survive
// a refresh — track it here instead and reapply it after every rebuild.
let appsScrollY = 0;
// Which destructive-action confirm dialog is currently showing, if any:
// null | "erase" | "uninstall". MUST live here, not just injected into the
// DOM — #root is fully rebuilt every ~1s live tick (see renderSettings()),
// so a confirm dialog set only via a stray innerHTML write gets silently
// wiped by the next tick, making it look like it "auto-collapsed" seconds
// after opening. Same root cause as the Apps-list scroll-reset bug.
let dangerConfirm = null;

function step(d){
  if(tab==="day"){cur.setDate(cur.getDate()+7*d); renderDay();}
  else if(tab==="year"){yr+=d; renderYear();}
  else if(tab==="alltime"){ /* nothing to navigate — one fixed total */ }
  else { // apps: step by month
    let [y,m]=appMonth.split("-").map(Number); m+=d;
    if(m<1){m=12;y--;} if(m>12){m=1;y++;}
    appMonth=y+"-"+String(m).padStart(2,"0"); renderApps();
  }
}

function renderDay(){
  const [s,e]=weekRange(cur);
  document.getElementById("navlabel").textContent=
    MONS[s.getMonth()]+" "+s.getDate()+" – "+(e.getMonth()!==s.getMonth()?MONS[e.getMonth()]+" ":"")+e.getDate();
  let h='<div class="grid7">'+DOW.map(x=>'<div class="dow">'+x[0]+'</div>').join('');
  for(let i=0;i<7;i++){ const dt=new Date(s); dt.setDate(s.getDate()+i); const k=dkey(dt);
    const [d,u]=get(k); const has=d+u>0;
    const cls="cell"+(k===sel?" sel":"")+(has?"":" empty");
    const v=has?('<div class="v"><div class="d">↓'+short(d)+'</div><div class="u">↑'+short(u)+'</div></div>'):'<div class="v"></div>';
    h+='<div class="'+cls+'" data-k="'+k+'"><div class="dn-n">'+dt.getDate()+'</div>'+v+'</div>';
  }
  h+='</div>';
  document.getElementById("calbody").innerHTML=h;
  document.querySelectorAll("#calbody .cell:not(.empty)").forEach(c=>c.onclick=()=>{sel=c.dataset.k; renderDay(); renderBig(); renderNet();});
  renderBig(); renderNet();
}

function renderBig(){
  const dt=new Date(sel+"T12:00:00"); const [d,u]=get(sel);
  document.getElementById("bigcard").innerHTML=
    '<div class="when">'+DOW[dt.getDay()]+', '+MONS[dt.getMonth()]+' '+dt.getDate()+'</div>'
    +'<div class="tot">'+human(d+u)+'</div>'
    +'<div class="sub"><span class="d">↓ '+human(d)+'</span> &nbsp; <span class="u">↑ '+human(u)+'</span></div>';
}
function renderNet(){
  document.getElementById("netcard").innerHTML=netTableHTML(DATA.networks[sel]);
}

function renderYear(){
  document.getElementById("navlabel").textContent=String(yr);
  const mtot={}; let ytd=0,ytu=0;
  for(const k in DATA.daily){ if(k.slice(0,4)!=String(yr)) continue;
    const m=parseInt(k.slice(5,7),10); const [d,u]=DATA.daily[k];
    if(!mtot[m])mtot[m]=[0,0]; mtot[m][0]+=d; mtot[m][1]+=u; ytd+=d; ytu+=u; }
  const curY=new Date(DATA.today+"T12:00:00"); const curMonth=curY.getFullYear()===yr?curY.getMonth()+1:0;
  // selMonth === null means "whole year"; otherwise a month number 1-12.
  let h='<div class="months">';
  for(let m=1;m<=12;m++){ const t=mtot[m]; const has=!!t;
    const on=(selMonth===m);
    const cls="mtile"+(on?" cur":(m===curMonth&&selMonth===null?" cur":""))+(has?"":" empty");
    const v=has?('<div class="mv"><span class="d">↓'+short(t[0])+'</span> <span class="u">↑'+short(t[1])+'</span></div>')
      :'<div class="mv">—</div>';
    h+='<div class="'+cls+'" data-m="'+m+'"'+(has?' style="cursor:pointer"':'')
      +'><div class="mn">'+MONS[m-1]+'</div>'+v+'</div>';
  }
  h+='</div>';
  document.getElementById("calbody").innerHTML=h;

  // Big card + network table follow the selection: a month if one is picked,
  // otherwise the whole year.
  let label, bd, bu, want;
  if(selMonth!==null && mtot[selMonth]){
    label=MONS[selMonth-1]+" "+yr; bd=mtot[selMonth][0]; bu=mtot[selMonth][1];
    want=yr+"-"+String(selMonth).padStart(2,"0");
  } else {
    label=String(yr); bd=ytd; bu=ytu; want=null;
  }
  document.getElementById("bigcard").innerHTML=
    '<div class="when">'+label+'</div><div class="tot">'+human(bd+bu)+'</div>'
    +'<div class="sub"><span class="d">↓ '+human(bd)+'</span> &nbsp; <span class="u">↑ '+human(bu)+'</span></div>';

  const nagg={};
  for(const k in DATA.networks){
    if(want ? k.slice(0,7)!==want : k.slice(0,4)!=String(yr)) continue;
    for(const [nm,d,u] of DATA.networks[k]){ if(!nagg[nm])nagg[nm]=[0,0]; nagg[nm][0]+=d; nagg[nm][1]+=u; } }
  const list=Object.entries(nagg).map(([nm,v])=>[nm,v[0],v[1]]).sort((a,b)=>(b[1]+b[2])-(a[1]+a[2]));
  document.getElementById("netcard").innerHTML=netTableHTML(list);

  // Tap a month to drill in; tap it again to go back to the year.
  document.querySelectorAll("#calbody .mtile:not(.empty)").forEach(c=>c.onclick=()=>{
    const m=+c.dataset.m;
    selMonth=(selMonth===m)?null:m;
    renderYear(); requestResize();
  });
}

function renderApps(){
  const [y,m]=appMonth.split("-").map(Number);
  document.getElementById("navlabel").textContent=MONS[m-1]+" "+y;
  const bc=document.getElementById("bigcard");
  const nc=document.getElementById("netcard");
  document.getElementById("calbody").innerHTML='';

  // If per-app tracking is turned off, don't show a list — explain how to turn
  // it on instead.
  if(DATA.settings && DATA.settings.app_tracking===false){
    bc.innerHTML='<div class="when">'+MONS[m-1]+' '+y+'</div>'
      +'<div class="tot" style="font-size:14px;opacity:.55">App tracking is off</div>';
    nc.innerHTML='<div style="padding:14px 6px;text-align:center;font-size:12px;'
      +'color:var(--txt-dim);line-height:1.5">'
      +'Turn on <b>Track apps</b> in Settings to see which apps use your data.'
      +'<div style="margin-top:10px"><button id="go-settings" style="font-size:12px;'
      +'padding:6px 14px;border-radius:8px;border:0.5px solid var(--glass-border);'
      +'background:var(--glass-strong);cursor:pointer;color:var(--txt)">Open Settings</button></div>'
      +'</div>';
    const b=document.getElementById("go-settings");
    if(b) b.onclick=()=>{view="settings"; render(); requestResize();};
    return;
  }

  // The CURRENT month is live and must never be served from the cache — native
  // pushes a fresh payload on every beat. Only SEALED (past) months are cached,
  // because their numbers can no longer change.
  const curMon=(DATA.today||"").slice(0,7);
  let ad=null;
  if(appMonth===curMon){
    ad=(DATA.appData && DATA.appData.value===appMonth) ? DATA.appData : null;
  } else {
    ad=appCache[appMonth]||null;
  }
  if(!ad && appPending!==appMonth){
    appPending=appMonth;
    bridge("appmonth:"+appMonth);          // ask native for this month
  }
  const haveData = !!ad;

  if(!haveData){
    // Either not loaded yet, or we've navigated to a month native hasn't sent.
    bc.innerHTML='<div class="when">'+MONS[m-1]+' '+y+'</div>'
      +'<div class="tot" style="font-size:14px;opacity:.6">'
      +(appPending===appMonth?'Loading '+MONS[m-1]+' '+y+'…':'No data for '+MONS[m-1]+' '+y)+'</div>';
    nc.innerHTML='';
    return;
  }

  // The total counts REAL APPS only. System / local-network services (Bonjour,
  // AirDrop, Continuity) are shown separately and never counted — they are not
  // what "which app used my internet data" is asking about.
  const tot=ad.total||{down:0,up:0};
  let td=tot.down||0, tu=tot.up||0;
  if(!ad.total){ td=0; tu=0; for(const a of (ad.apps||[])){td+=a.down; tu+=a.up;} }
  bc.innerHTML='<div class="when">'+MONS[m-1]+' '+y+'</div>'
    +'<div class="tot">'+human(td+tu)+'</div>'
    +'<div class="sub"><span class="d">↓ '+human(td)+'</span> &nbsp; <span class="u">↑ '+human(tu)+'</span></div>';

  if(!ad.apps.length && !(ad.system||[]).length){
    nc.innerHTML='<div class="nethead"><span class="nm">App</span><span class="d">↓ Down</span><span class="u">↑ Up</span></div>'
      +'<div class="netrow"><span class="nm" style="opacity:.6">Collecting per-app data… check back in a minute.</span></div>';
    return;
  }

  // A marker only appears when something is arithmetically inconsistent, so an
  // unmarked row means normal. No green dots — 25 of them would hide the two
  // that matter.
  function dot(a){
    if(!a.flag) return '';
    const c=(a.flag==="red")?"#c0402a":"#d08a1e";
    return '<span class="aflag" data-r="'+(a.reason||"").replace(/"/g,"&quot;")+'"'
      +' style="display:inline-block;width:7px;height:7px;border-radius:50%;'
      +'background:'+c+';margin-right:6px;vertical-align:middle;cursor:pointer"></span>';
  }
  function rowHTML(a,i,dim){
    const open=!!appExpanded[a.app];
    const fn=a.friendly||a.app;
    const raw=(fn!==a.app)?(' <span style="opacity:.45;font-size:10px">'+a.app+'</span>'):'';
    const arrow=(a.networks&&a.networks.length)?(open?"▾":"▸"):"&nbsp;";
    let r='<div class="netrow approw" data-i="'+i+'" style="cursor:pointer'+(dim?';opacity:.55':'')+'">'
      +'<span class="nm"><span style="display:inline-block;width:12px;opacity:.5">'+arrow+'</span>'
      +dot(a)+fn+raw+'</span>'
      +'<span class="d" style="color:var(--dn)">'+human(a.down)+'</span>'
      +'<span class="u" style="color:var(--up)">'+human(a.up)+'</span></div>';
    if(open && a.networks){
      for(const n of a.networks){
        r+='<div class="netrow" style="padding-left:20px;font-size:11px;opacity:.75">'
          +'<span class="nm">'+n.network+'</span>'
          +'<span class="d">'+human(n.down)+'</span>'
          +'<span class="u">'+human(n.up)+'</span></div>';
      }
    }
    return r;
  }

  let h='<div class="nethead"><span class="nm">App</span><span class="d">↓ Down</span><span class="u">↑ Up</span></div>';
  ad.apps.forEach((a,i)=>{ h+=rowHTML(a,i,false); });

  const sys=ad.system||[];
  const showSys=!(DATA.settings && DATA.settings.show_system_apps===false);
  if(sys.length && showSys){
    let sd=0,su=0; for(const a of sys){sd+=a.down; su+=a.up;}
    h+='<div style="padding:10px 2px 4px;margin-top:6px;border-top:0.5px solid var(--glass-border-soft);'
      +'font-size:10px;text-transform:uppercase;letter-spacing:.3px;color:var(--txt-faint)">'
      +'System &amp; local network · not counted</div>';
    sys.forEach((a,i)=>{ h+=rowHTML(a,1000+i,true); });
  }
  h+='<div id="flagmsg" style="font-size:11px;color:var(--txt-dim);padding:8px 2px 0;line-height:1.5"></div>';
  // Grow the list to fit the available screen before it has to scroll
  // internally — a fixed cap meant the list started scrolling long before
  // the screen was actually out of room. window.screen.availHeight is the
  // real usable height (menu bar already excluded); RESERVED accounts for
  // everything else in the panel (speed card, tabs/nav, total card, the
  // Settings/Quit row, margins) so those never get pushed off-panel. Falls
  // back to the old 330px if screen info isn't available for some reason.
  // NOTE: RESERVED is an estimate — tune against the actual built panel.
  const RESERVED_FOR_REST_OF_PANEL = 430;
  const maxListHeight = Math.max(330,
    (window.screen && window.screen.availHeight)
      ? window.screen.availHeight - RESERVED_FOR_REST_OF_PANEL
      : 330);
  nc.innerHTML='<div class="applist" style="max-height:'+maxListHeight+'px;overflow-y:auto;'
    +'overflow-x:hidden;-webkit-overflow-scrolling:touch">'+h+'</div>';

  // Restore scroll position after the rebuild, and keep tracking it as the
  // user scrolls, so the NEXT rebuild (in ~1s) can restore it too.
  const listEl=nc.querySelector(".applist");
  if(listEl){
    listEl.scrollTop=appsScrollY;
    listEl.addEventListener("scroll", ()=>{ appsScrollY=listEl.scrollTop; });
  }

  const all=ad.apps.concat(sys);
  document.querySelectorAll("#netcard .approw").forEach(r=>r.onclick=()=>{
    const i=+r.dataset.i;
    const a=(i>=1000)?sys[i-1000]:ad.apps[i];
    if(!a||!a.networks||!a.networks.length) return;
    appExpanded[a.app]=!appExpanded[a.app]; renderApps(); requestResize();
  });
  // Tap a marker for the reason in plain words.
  document.querySelectorAll("#netcard .aflag").forEach(d=>d.onclick=(e)=>{
    e.stopPropagation();
    const box=document.getElementById("flagmsg");
    if(box){ box.textContent=d.dataset.r||""; requestResize(); }
  });
}

function timeAgo(iso){
  try{
    const s=Math.max(0, Math.floor((Date.now()-new Date(iso).getTime())/1000));
    if(s<60) return "just now";
    const m=Math.floor(s/60); if(m<60) return m+" min"+(m===1?"":"s")+" ago";
    const h=Math.floor(m/60); if(h<24) return h+" hour"+(h===1?"":"s")+" ago";
    const d=Math.floor(h/24); return d+" day"+(d===1?"":"s")+" ago";
  }catch(e){ return ""; }
}

// All-time tab: a manually-generated total, NOT part of the live refresh —
// scanning every month file on every ~1s tick would reintroduce the exact
// full-history-rescan problem the whole split-by-month store was built to
// avoid (see the storage rebuild notes). Native computes it once per tap and
// caches the result in settings, so it also survives a relaunch.
function renderAllTime(){
  document.getElementById("navlabel").textContent="All-time";
  document.getElementById("calbody").innerHTML="";
  const bc=document.getElementById("bigcard");
  const nc=document.getElementById("netcard");
  const ac=(DATA.settings && DATA.settings.alltime_cache) || {};

  if(!ac.generated_at){
    bc.innerHTML='<div class="when">All-time total</div>'
      +'<div class="tot" style="font-size:15px;opacity:.6">Not yet generated</div>';
    nc.innerHTML='<div style="padding:4px 2px 14px;text-align:center;font-size:12px;color:var(--txt-dim)">'
      +'Tap Generate to add up every month on record.</div>'
      +'<button class="btn" id="gen-alltime" style="width:100%"><span class="ic">&#8635;</span>Generate</button>';
  } else {
    const since=ac.first_day
      ? (" &middot; since "+MONS[+ac.first_day.slice(5,7)-1]+" "+ac.first_day.slice(0,4))
      : "";
    bc.innerHTML='<div class="when">All-time total</div>'
      +'<div class="tot">'+human((ac.down||0)+(ac.up||0))+'</div>'
      +'<div class="sub"><span class="d">&#8595; '+human(ac.down||0)+'</span> &nbsp; '
      +'<span class="u">&#8593; '+human(ac.up||0)+'</span></div>';
    nc.innerHTML='<div style="padding:4px 2px 14px;text-align:center;font-size:12px;color:var(--txt-dim)">'
      +(ac.months||0)+' month'+((ac.months===1)?'':'s')+' recorded'+since+'</div>'
      +'<button class="btn" id="gen-alltime" style="width:100%"><span class="ic">&#8635;</span>Generate</button>'
      +'<div style="text-align:center;font-size:11px;color:var(--txt-dim);margin-top:8px">'
      +'Last generated '+timeAgo(ac.generated_at)+'</div>';
  }
  const btn=document.getElementById("gen-alltime");
  if(btn) btn.onclick=()=>{
    btn.disabled=true; btn.textContent="Generating…";
    bridge("alltime:generate");
  };
}

// Native pushes the freshly computed total here after a Generate tap.
window.__allTimeData=function(cache){
  try{
    DATA.settings=DATA.settings||{};
    DATA.settings.alltime_cache=cache||{};
    if(view==="main" && tab==="alltime"){ renderAllTime(); requestResize(); }
  }catch(e){}
};

// Native delivers a month the Apps tab asked for. Sealed (past) months are
// cached permanently — no timer, no refetch, no live updating.
window.__appMonthData=function(p){
  try{
    if(!p||!p.value) return;
    if(appPending===p.value) appPending=null;
    if(p.sealed){
      appCache[p.value]=p;      // frozen for good — keep it
    } else {
      DATA.appData=p;           // live month — never cache, it changes
      delete appCache[p.value];
    }
    if(view==="main" && tab==="apps") { renderApps(); requestResize(); }
  }catch(e){}
};

// Native calls this back with the result of a manual update check.
window.__updateResult=function(res){
  try{
    const m=document.getElementById("update-msg");
    if(!m) return;
    if(!res || res.status==="error"){
      m.innerHTML='<span style="color:var(--up)">'+((res&&res.message)||"Couldn't check for updates.")+'</span>';
    } else if(res.status==="current"){
      m.innerHTML='<span style="color:var(--dn)">You\u2019re up to date (v'+res.latest+').</span>';
    } else if(res.status==="update"){
      let h='<b>Version '+res.latest+' is available.</b>';
      if(res.notes) h+='<br>'+res.notes;
      h+='<br><a href="#" id="update-link" style="color:#0076f5;text-decoration:none">Download the update \u2192</a>';
      m.innerHTML=h;
      const a=document.getElementById("update-link");
      if(a) a.onclick=(e)=>{e.preventDefault(); bridge("openurl:"+(res.url||""));};
    }
  }catch(e){}
};

let acc={networks:true, appearance:false, export:false};  // which sections open
let expandedNetIdx=null;   // which network row is expanded inline (or null)

// Inline detail panel shown beneath an expanded network row (replaces the old
// separate "netdetail" view). Rename / Count-in-total / Remove, all in place.
function netDetailInline(n,i){
  const tot=human((n.down||0)+(n.up||0));
  let h='<div class="netdetail" style="padding:4px 2px 10px;border-top:0.5px solid rgba(0,0,0,0.05);background:rgba(0,118,245,0.03);margin:0 -14px;padding-left:22px;padding-right:22px">';
  h+='<div class="srow" id="ndi-rename" style="cursor:pointer"><span>Name</span><span style="color:var(--txt-secondary)">'+(n.custom||n.display)+' ›</span></div>';
  h+='<div class="srow"><span>Count in total</span><div class="sw '+(n.excluded?'':'on')+'" id="ndi-count"><div class="kn"></div></div></div>';
  h+='<div class="srow"><span style="color:var(--txt-faint)">Total data</span><span style="color:var(--txt-faint)">'+tot+'</span></div>';
  h+='<div class="srow" id="ndi-delete" style="cursor:pointer;border-bottom:none"><span style="color:#c0402a">Remove this network</span></div>';
  h+='<div id="ndi-confirm" style="font-size:11px;color:var(--txt-dim);padding:2px 2px 0;line-height:1.5"></div>';
  h+='</div>';
  return h;
}

function wireNetDetailInline(n,i){
  const rn=document.getElementById("ndi-rename");
  if(rn) rn.onclick=()=>startNetRenameInline(n,i);
  const ct=document.getElementById("ndi-count");
  if(ct) ct.onclick=()=>{
    const nowEx=!n.excluded; n.excluded=nowEx;
    ct.classList.toggle("on", !nowEx);
    bridge("netexclude:"+n.fp+":"+(nowEx?"1":"0"));
    renderSettings(); requestResize();
  };
  const del=document.getElementById("ndi-delete");
  if(del) del.onclick=()=>{
    const c=document.getElementById("ndi-confirm");
    c.innerHTML='<b>Remove '+n.display+' and all its data?</b> This can\u2019t be undone.'
      +'<div style="margin-top:8px;display:flex;gap:8px">'
      +'<button id="ndi-yes" style="font-size:12px;padding:6px 14px;border-radius:8px;border:0.5px solid rgba(224,96,63,0.4);background:rgba(224,96,63,0.12);color:#c0402a;cursor:pointer">Remove</button>'
      +'<button id="ndi-no" style="font-size:12px;padding:6px 14px;border-radius:8px;border:0.5px solid var(--glass-border);background:var(--glass-strong);cursor:pointer;color:var(--txt)">Cancel</button>'
      +'</div>';
    document.getElementById("ndi-yes").onclick=()=>{
      bridge("netdelete:"+n.fp);
      // Drop it from the local copy right away so the row disappears on tap,
      // rather than waiting for the next payload from native.
      try{ DATA.knownNetworks.splice(expandedNetIdx,1); }catch(e){}
      expandedNetIdx=null;
      renderSettings(); requestResize();
    };
    document.getElementById("ndi-no").onclick=()=>{ c.innerHTML=''; requestResize(); };
    requestResize();
  };
}

function startNetRenameInline(n,i){
  const c=document.getElementById("ndi-rename");
  const cur=n.custom||"";
  c.innerHTML='<input id="ndi-input" value="'+cur.replace(/"/g,'&quot;')+'" placeholder="Network name" style="flex:1;font-size:13px;height:30px;border:0.5px solid #0076f5;border-radius:6px;padding:0 8px;background:var(--glass-strong)"/>'
    +'<span id="ndi-save" style="cursor:pointer;color:#0076f5;font-size:13px;padding:2px 10px;border-radius:6px;margin-left:8px">Save</span>';
  c.style.cursor='default';
  const inp=document.getElementById("ndi-input"); inp.focus(); inp.select();
  const btn=document.getElementById("ndi-save");
  const orig=(cur||"");
  const setDirty=()=>{
    const changed=inp.value.trim()!==orig;
    btn.textContent="Save";
    btn.style.color=changed?"#0076f5":"var(--txt-faint)";
    btn.style.cursor=changed?"pointer":"default";
    btn.dataset.dirty=changed?"1":"";
  };
  setDirty();
  inp.oninput=setDirty;
  const save=()=>{
    if(btn.dataset.dirty!=="1") return;
    const v=inp.value.trim();
    bridge("rename:"+n.fp+":"+encodeURIComponent(v));
    n.custom=v||null; n.display=v||n.display;
    btn.textContent="Saved";
    btn.style.color="var(--txt-faint)";
    btn.style.cursor="default";
    btn.dataset.dirty="";
    setTimeout(()=>{ renderSettings(); requestResize(); }, 600);
  };
  btn.onclick=save;
  inp.onkeydown=(e)=>{ if(e.key==="Enter") save(); };
}

function accHeader(key,label){
  const open=acc[key];
  return '<div class="acch" data-acc="'+key+'" style="padding:11px 14px;display:flex;align-items:center;cursor:pointer'+(open?';background:rgba(0,118,245,0.05)':'')+'">'
    +'<span style="flex:1;font-size:12.5px;font-weight:500">'+label+'</span>'
    +'<span style="font-size:14px;color:var(--txt-faint)">'+(open?'⌄':'›')+'</span></div>';
}

function dangerConfirmHTML(kind){
  if(kind==="erase"){
    return '<b>Erase all recorded data?</b> This deletes every network and per-app record and can\u2019t be undone. Your settings are kept.'
      +'<div style="margin-top:8px;display:flex;gap:8px">'
      +'<button id="er-yes" style="font-size:12px;padding:6px 14px;border-radius:8px;border:0.5px solid var(--glass-border);background:var(--glass-strong);color:#c0402a;cursor:pointer">Erase everything</button>'
      +'<button id="er-no" style="font-size:12px;padding:6px 14px;border-radius:8px;border:0.5px solid var(--glass-border);background:var(--glass-strong);cursor:pointer;color:var(--txt)">Cancel</button>'
      +'</div>';
  }
  if(kind==="erased"){
    return '<span style="color:var(--txt-dim)">All data erased.</span>';
  }
  if(kind==="uninstall"){
    return '<b>Uninstall NUM?</b> This quits the app, unregisters "launch at '
      +'login" if it was on, removes it from Applications, and permanently '
      +'erases all saved usage data and settings. This can\u2019t be undone. '
      +'If you just want to clear your history and keep using NUM, use '
      +'"Erase all data" above instead.'
      +'<div style="margin-top:8px;display:flex;gap:8px">'
      +'<button id="un-yes" style="font-size:12px;padding:6px 14px;border-radius:8px;border:0.5px solid var(--glass-border);background:var(--glass-strong);color:#c0402a;cursor:pointer">Uninstall</button>'
      +'<button id="un-no" style="font-size:12px;padding:6px 14px;border-radius:8px;border:0.5px solid var(--glass-border);background:var(--glass-strong);cursor:pointer;color:var(--txt)">Cancel</button>'
      +'</div>';
  }
  if(kind==="uninstalling"){
    return '<span style="color:var(--txt-dim)">Uninstalling\u2026 NUM will quit in a moment.</span>';
  }
  return '';
}

function renderSettings(){
  const s=DATA.settings;
  let h='<div class="shead"><span class="back" id="back">‹</span><span class="ttl">Settings</span></div>';

  // ---- Top tier: flat toggles + menu number ----
  h+='<div class="card">'
    +'<div class="srow"><span>Track network</span><div class="sw '+(s.tracking?"on":"")+'" id="sw-track"><div class="kn"></div></div></div>'
    +'<div class="srow"><span>Track apps</span><div class="sw '+(s.app_tracking?"on":"")+'" id="sw-apps"><div class="kn"></div></div></div>'
    +(s.app_tracking?('<div class="srow" style="padding-left:14px"><span style="color:var(--txt-secondary)">Update apps every</span>'
      +'<div class="seg" id="seg-appiv">'
      +'<span class="segopt '+((s.app_sample_interval||30)==10?"on":"")+'" data-v="10">10s</span>'
      +'<span class="segopt '+((s.app_sample_interval||30)==30?"on":"")+'" data-v="30">30s</span>'
      +'<span class="segopt '+((s.app_sample_interval||30)==60?"on":"")+'" data-v="60">60s</span>'
      +'</div></div>'
      +'<div class="srow" style="padding-left:14px"><span style="color:var(--txt-secondary)">Show system &amp; local services</span>'
      +'<div class="sw '+(s.show_system_apps?"on":"")+'" id="sw-sysapps"><div class="kn"></div></div></div>'):'')
    +'<div class="srow"><span>Show network names</span><div class="sw '+(s.network_names?"on":"")+'" id="sw-netnames"><div class="kn"></div></div></div>'
    +'<div class="srow"><span>Launch at login</span><div class="sw '+(s.launch_at_login?"on":"")+'" id="sw-login"><div class="kn"></div></div></div>'
    +'<div class="srow"><span>Show number in menu bar</span><div class="sw '+(s.show_menu_text?"on":"")+'" id="sw-menu"><div class="kn"></div></div></div>'
    +(s.show_menu_text?('<div class="srow" style="padding-left:14px"><span style="color:var(--txt-secondary)">Menu bar shows</span>'
      +'<div class="seg" id="seg-menunum">'
      +'<span class="segopt '+((s.menu_number||"total")==="down"?"on":"")+'" data-v="down">↓ Down</span>'
      +'<span class="segopt '+((s.menu_number||"total")==="up"?"on":"")+'" data-v="up">↑ Up</span>'
      +'<span class="segopt '+((s.menu_number||"total")==="total"?"on":"")+'" data-v="total">Total</span>'
      +'</div></div>'
):'')
    +'</div>';

  // ---- Accordion sections ----
  h+='<div class="card" style="padding:0;overflow:hidden;margin-top:12px">';

  // Networks
  h+=accHeader("networks","Networks");
  if(acc.networks){
    const nets=DATA.knownNetworks||[];
    h+='<div style="padding:0 14px 8px">';
    if(!nets.length){
      h+='<div style="padding:12px 2px;font-size:12px;color:var(--txt-dim);text-align:center">No networks recorded yet.</div>';
    } else {
      nets.forEach((n,i)=>{
        const tot=human((n.down||0)+(n.up||0));
        const open=(expandedNetIdx===i);
        h+='<div class="netmrow" data-i="'+i+'" style="display:flex;align-items:center;gap:8px;padding:8px 2px;border-top:0.5px solid var(--glass-border-soft);cursor:pointer;'+(n.excluded?'opacity:.5':'')+'">'
          +'<span style="flex:1;min-width:0"><span style="font-size:12.5px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+n.display+'</span>'
          +'<span style="font-size:10px;color:var(--txt-faint)">'+tot+(n.excluded?' · not counted':'')+'</span></span>'
          +'<span style="color:var(--txt-faint);font-size:14px;transition:transform .15s">'+(open?'⌄':'›')+'</span></div>';
        if(open){ h+=netDetailInline(n,i); }
      });
    }
    h+='</div>';
  }

  // Appearance
  h+='<div style="border-top:0.5px solid var(--glass-border-soft)"></div>';
  h+=accHeader("appearance","Appearance");
  if(acc.appearance){
    const theme=s.theme||"system";
    h+='<div style="padding:2px 14px 8px">'
      +'<div class="srow"><span>Theme</span>'
      +'<div class="seg" id="seg-theme">'
      +'<span class="segopt '+(theme==="system"?"on":"")+'" data-v="system">System</span>'
      +'<span class="segopt '+(theme==="light"?"on":"")+'" data-v="light">Light</span>'
      +'<span class="segopt '+(theme==="dark"?"on":"")+'" data-v="dark">Dark</span>'
      +'</div></div>'
      +'<div class="srow"><span>Transparency</span><input type="range" min="0" max="100" value="'+(s.transparency==null?15:s.transparency)+'" id="rng-trans"></div>'
      +'<div class="srow" style="padding-bottom:2px"><span></span>'
      +'<span style="display:flex;gap:16px;font-size:10px;color:var(--txt-faint);text-transform:uppercase;letter-spacing:.3px">'
      +'<span style="width:26px;text-align:center">Light</span>'
      +'<span style="width:26px;text-align:center">Dark</span>'
      +'<span id="col-reset" title="Reset to default colours" style="cursor:pointer;color:var(--txt-dim);font-size:14px;line-height:1;padding:0 2px">&#8635;</span>'
      +'</span></div>'
      +'<div class="srow"><span>Download colour</span><span style="display:flex;gap:16px;align-items:center">'
      +'<div class="cpick" id="col-dn-light" style="background:'+s.down_color_light+'"></div>'
      +'<div class="cpick" id="col-dn-dark" style="background:'+s.down_color_dark+'"></div>'
      +'</span></div>'
      +'<div class="srow"><span>Upload colour</span><span style="display:flex;gap:16px;align-items:center">'
      +'<div class="cpick" id="col-up-light" style="background:'+s.up_color_light+'"></div>'
      +'<div class="cpick" id="col-up-dark" style="background:'+s.up_color_dark+'"></div>'
      +'</span></div>'
      +'</div>';
  }

  // Export & data
  h+='<div style="border-top:0.5px solid var(--glass-border-soft)"></div>';
  h+=accHeader("export","Export & data");
  if(acc.export){
    h+='<div style="padding:2px 14px 10px">'
      +'<div id="export-card">'+exportHTML()+'</div>'
      +'<div class="sitem" id="it-folder" style="padding:9px 0">Open data folder</div>'
      +'<div class="sitem" id="it-update" style="padding:9px 0">Check for updates<span style="margin-left:auto;font-size:11px;color:var(--txt-faint)">Version '+DATA.version+'</span></div>'
      +'<div id="update-msg" style="font-size:11px;color:var(--txt-dim);padding:0 2px 6px;line-height:1.5"></div>'
      +'<div class="sitem" id="it-erase" style="padding:9px 0;border-top:0.5px solid var(--glass-border-soft)"><span style="color:#c0402a">Erase all data</span></div>'
      +'<div id="erase-confirm" style="font-size:11px;color:var(--txt-dim);padding:0 2px 6px;line-height:1.5">'
      +(dangerConfirm==="erase"||dangerConfirm==="erased" ? dangerConfirmHTML(dangerConfirm) : '')
      +'</div>'
      +'</div>';
  }
  h+='</div>';

  // Uninstall — deliberately its OWN fixed section, always visible with no
  // accordion to expand, not tucked inside Export & data. Same reasoning as
  // Settings/Quit at the bottom of the main tab: something you want to find
  // reliably, not something that should depend on remembering it's inside a
  // collapsed section.
  h+='<div class="card" style="margin-top:12px">'
    +'<div class="sitem" id="it-uninstall" style="padding:9px 0"><span style="color:#c0402a">Uninstall NUM</span></div>'
    +'<div id="uninstall-confirm" style="font-size:11px;color:var(--txt-dim);padding:0 2px 6px;line-height:1.5">'
    +(dangerConfirm==="uninstall"||dangerConfirm==="uninstalling" ? dangerConfirmHTML(dangerConfirm) : '')
    +'</div>'
    +'</div>';

  document.getElementById("root").innerHTML=h;
  document.getElementById("foot").textContent="NUM v"+DATA.version+" · by "+DATA.author;

  document.getElementById("back").onclick=()=>{view="main"; render(); requestResize();};
  document.getElementById("sw-track").onclick=()=>toggle("tracking","sw-track");
  document.getElementById("sw-apps").onclick=()=>toggle("app_tracking","sw-apps");
  document.getElementById("sw-netnames").onclick=()=>toggle("network_names","sw-netnames");
  document.getElementById("sw-menu").onclick=()=>toggle("show_menu_text","sw-menu");
  const seg=document.getElementById("seg-menunum");
  if(seg) seg.querySelectorAll(".segopt").forEach(o=>o.onclick=()=>{
    DATA.settings.menu_number=o.dataset.v;
    seg.querySelectorAll(".segopt").forEach(x=>x.classList.toggle("on", x===o));
    bridge("set:menu_number:"+o.dataset.v);
  });
  document.getElementById("sw-login").onclick=()=>toggle("launch_at_login","sw-login");
  const swsys=document.getElementById("sw-sysapps");
  if(swsys) swsys.onclick=()=>toggle("show_system_apps","sw-sysapps");
  const segiv=document.getElementById("seg-appiv");
  if(segiv) segiv.querySelectorAll(".segopt").forEach(o=>o.onclick=()=>{
    DATA.settings.app_sample_interval=parseInt(o.dataset.v,10);
    segiv.querySelectorAll(".segopt").forEach(x=>x.classList.toggle("on", x===o));
    bridge("set:app_sample_interval:"+o.dataset.v);
  });
  const segtheme=document.getElementById("seg-theme");
  if(segtheme) segtheme.querySelectorAll(".segopt").forEach(o=>o.onclick=()=>{
    DATA.settings.theme=o.dataset.v;
    segtheme.querySelectorAll(".segopt").forEach(x=>x.classList.toggle("on", x===o));
    applyTheme(); applyColors();
    bridge("set:theme:"+o.dataset.v);
  });

  // Accordion header clicks
  document.querySelectorAll(".acch").forEach(hd=>hd.onclick=()=>{
    const k=hd.dataset.acc; acc[k]=!acc[k]; renderSettings(); requestResize();
  });

  // Networks rows (only wired when section open) — expand/collapse inline
  document.querySelectorAll(".netmrow").forEach(r=>r.onclick=()=>{
    const i=+r.dataset.i;
    expandedNetIdx=(expandedNetIdx===i)?null:i;
    renderSettings(); requestResize();
  });
  // Wire the inline detail controls for whichever row is expanded
  if(acc.networks && expandedNetIdx!=null){
    const n=(DATA.knownNetworks||[])[expandedNetIdx];
    if(n) wireNetDetailInline(n, expandedNetIdx);
  }

  // Appearance controls (only when open)
  const rng=document.getElementById("rng-trans");
  if(rng) rng.oninput=(e)=>{ DATA.settings.transparency=parseInt(e.target.value,10);
    applyColors(); bridge("set:transparency:"+DATA.settings.transparency); };
  const cdl=document.getElementById("col-dn-light"); if(cdl) cdl.onclick=()=>bridge("pickcolor:down_light");
  const cul=document.getElementById("col-up-light"); if(cul) cul.onclick=()=>bridge("pickcolor:up_light");
  const cdd=document.getElementById("col-dn-dark"); if(cdd) cdd.onclick=()=>bridge("pickcolor:down_dark");
  const cud=document.getElementById("col-up-dark"); if(cud) cud.onclick=()=>bridge("pickcolor:up_dark");
  const colReset=document.getElementById("col-reset");
  if(colReset) colReset.onclick=()=>bridge("resetcolors");

  // Export & data (only when open)
  const itf=document.getElementById("it-folder"); if(itf) itf.onclick=()=>bridge("folder");
  const upd=document.getElementById("it-update");
  if(upd) upd.onclick=()=>{ const m=document.getElementById("update-msg"); if(m) m.textContent="Checking…"; bridge("checkupdate"); };
  const er=document.getElementById("it-erase");
  if(er) er.onclick=()=>{ dangerConfirm="erase"; renderSettings(); requestResize(); };
  if(dangerConfirm==="erase"){
    const eyes=document.getElementById("er-yes"), eno=document.getElementById("er-no");
    if(eyes) eyes.onclick=()=>{
      bridge("erasealldata");
      expandedNetIdx=null;
      dangerConfirm="erased";
      renderSettings(); requestResize();
    };
    if(eno) eno.onclick=()=>{ dangerConfirm=null; renderSettings(); requestResize(); };
  }

  const un=document.getElementById("it-uninstall");
  if(un) un.onclick=()=>{ dangerConfirm="uninstall"; renderSettings(); requestResize(); };
  if(dangerConfirm==="uninstall"){
    const uyes=document.getElementById("un-yes"), uno=document.getElementById("un-no");
    if(uyes) uyes.onclick=()=>{
      dangerConfirm="uninstalling";
      renderSettings(); requestResize();
      bridge("uninstall_app");
    };
    if(uno) uno.onclick=()=>{ dangerConfirm=null; renderSettings(); requestResize(); };
  }
  if(acc.export) wireExport();
}

let exportMode="month";  // month | year | all
const MONNAMES=["January","February","March","April","May","June","July",
  "August","September","October","November","December"];

function exportHTML(){
  const months=DATA.months||[]; const years=DATA.years||[];
  // month dropdown: build from available YYYY-MM
  let monthOpts=months.map(m=>{
    const y=m.slice(0,4), mo=parseInt(m.slice(5,7),10);
    return '<option value="'+m+'">'+MONNAMES[mo-1]+' '+y+'</option>';
  }).join('');
  if(!monthOpts) monthOpts='<option value="">No data</option>';
  let yearOpts=years.map(y=>'<option value="'+y+'">'+y+'</option>').join('');
  if(!yearOpts) yearOpts='<option value="">No data</option>';

  let h='<div class="cap">Export report</div>';
  h+='<div class="exptabs">'
    +'<div class="etab'+(exportMode==="month"?" on":"")+'" data-m="month">Month</div>'
    +'<div class="etab'+(exportMode==="year"?" on":"")+'" data-m="year">Year</div>'
    +'<div class="etab'+(exportMode==="all"?" on":"")+'" data-m="all">All time</div>'
    +'</div>';
  if(exportMode==="month"){
    h+='<select id="exp-month" class="expsel">'+monthOpts+'</select>';
  } else if(exportMode==="year"){
    h+='<select id="exp-year" class="expsel">'+yearOpts+'</select>';
  } else {
    h+='<div class="expall">Exports your entire history.</div>';
  }
  h+='<button class="expbtn" id="exp-go">↓ Export CSV</button>';
  return h;
}

function wireExport(){
  document.querySelectorAll(".etab").forEach(t=>t.onclick=()=>{
    exportMode=t.dataset.m;
    document.getElementById("export-card").innerHTML=exportHTML();
    wireExport(); requestResize();
  });
  const go=document.getElementById("exp-go");
  if(go) go.onclick=()=>{
    if(exportMode==="month"){
      const v=document.getElementById("exp-month").value;
      if(v) bridge("export:month:"+v);
    } else if(exportMode==="year"){
      const v=document.getElementById("exp-year").value;
      if(v) bridge("export:year:"+v);
    } else {
      bridge("export:all");
    }
  };
}

function toggle(key,id){
  DATA.settings[key]=!DATA.settings[key];
  document.getElementById(id).classList.toggle("on", DATA.settings[key]);
  bridge("set:"+key+":"+(DATA.settings[key]?"1":"0"));
}

function render(){ applyTheme(); applyColors(); if(view==="settings") renderSettings(); else renderMain(); }

// Live update from native without losing view/tab/selection
window.__updateData=function(d){
  try{
    if(d.daily) DATA.daily=d.daily;
    if(d.networks) DATA.networks=d.networks;
    if(d.net!==undefined) DATA.net=d.net;
    if(d.downBps!==undefined) DATA.downBps=d.downBps;
    if(d.upBps!==undefined) DATA.upBps=d.upBps;
    if(d.generated) DATA.generated=d.generated;
    if(d.appData) DATA.appData=d.appData;
    // Keep the Settings network list current, so a rename / exclude / delete
    // shows up without rebuilding the panel.
    if(d.knownNetworks){
      DATA.knownNetworks=d.knownNetworks;
      if(view==="settings" && expandedNetIdx===null){ renderSettings(); }
    }
    if(view==="main") render();
  }catch(e){}
};

render();
</script>
</body></html>"""
