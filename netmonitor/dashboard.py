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

VERSION = "1.0.0"
AUTHOR = "Charaka (@charaka-codes)"


def _gather(con):
    rows = con.execute(
        "SELECT substr(ts,1,10) d, SUM(down), SUM(up) FROM usage GROUP BY d"
    ).fetchall()
    return {d: [down, up] for d, down, up in rows}


def _gather_networks(con):
    rows = con.execute(
        "SELECT substr(ts,1,10) d, network, SUM(down), SUM(up) FROM usage "
        "GROUP BY d, network ORDER BY d, SUM(down)+SUM(up) DESC").fetchall()
    out = {}
    for d, net, down, up in rows:
        out.setdefault(d, []).append([net, down, up])
    return out


def build_html(con, month=None):
    now = datetime.now()
    down_bps, up_bps = core.recent_speed(con, seconds=8)
    iface = core.active_interface()
    net = core.network_label(iface) if iface else "offline"
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
    }
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
    --txt-faint: rgba(0,0,0,0.4);
    --line: rgba(0,0,0,0.08);
    --dn: #2e9e5b;
    --up: #e0603f;
    --sel-border: rgba(0,0,0,0.55);
    --blue: #3478f6;
  }
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
  .tabs button.active{background:#fff; color:var(--txt);}
  .nav{display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;}
  .nav button{border:none; background:transparent; font-size:18px; color:var(--txt-dim);
    cursor:pointer; padding:0 8px; line-height:1;}
  .nav .lbl{font-size:13px; font-weight:500;}

  .grid7{display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:3px;}
  .grid7 .dow{text-align:center; font-size:9px; color:var(--txt-faint);}
  .cell{min-width:0; height:60px; border-radius:8px; padding:4px 3px; display:flex; flex-direction:column;
    gap:1px; border:0.5px solid var(--card-border);
    background:rgba(255,255,255,0.5); cursor:pointer; overflow:hidden;}
  .cell .dn-n{font-size:10px; color:var(--txt);}
  .cell .v{line-height:1.25;}
  .cell .v .d{font-size:9px; color:var(--dn);}
  .cell .v .u{font-size:9px; color:var(--up);}
  .cell.empty{cursor:default; background:rgba(255,255,255,0.4);}
  .cell.empty .dn-n{color:var(--txt-faint);}
  .cell.sel{background:#fff; border:1.5px solid var(--sel-border);}
  .cell.sel .dn-n{font-weight:600;}

  .bigcard{text-align:center;}
  .bigcard .when{font-size:10px; color:var(--txt-dim); text-transform:uppercase; margin-bottom:4px;}
  .bigcard .tot{font-size:28px; font-weight:500;}
  .bigcard .sub{font-size:13px; margin-top:3px;}
  .bigcard .sub .d{color:var(--dn);} .bigcard .sub .u{color:var(--up);}

  .nethead{display:flex; font-size:10px; text-transform:uppercase; letter-spacing:.3px;
    color:var(--txt-faint); padding-bottom:5px; border-bottom:0.5px solid var(--line);}
  .nethead .nm{flex:1;} .nethead .d,.nethead .u{width:60px; text-align:right;}
  .netrow{display:flex; font-size:12px; padding:6px 0; border-bottom:0.5px solid rgba(0,0,0,0.05);}
  .netrow:last-child{border-bottom:none;}
  .netrow .nm{flex:1; color:var(--txt);}
  .netrow .d,.netrow .u{width:60px; text-align:right; color:#000;}

  .months{display:grid; grid-template-columns:repeat(3,1fr); gap:5px;}
  .mtile{border-radius:9px; padding:6px 7px; border:0.5px solid var(--card-border);
    background:rgba(255,255,255,0.7); cursor:pointer;}
  .mtile .mn{font-size:11px; color:var(--txt);}
  .mtile .mv{line-height:1.3; margin-top:3px;}
  .mtile .mv .d{font-size:9.5px; color:var(--dn);} .mtile .mv .u{font-size:9.5px; color:var(--up);}
  .mtile.empty{cursor:default; background:rgba(255,255,255,0.4);}
  .mtile.empty .mn{color:var(--txt-faint);} .mtile.empty .mv{color:var(--txt-faint); font-size:9.5px;}
  .mtile.cur{background:#fff; border:1.5px solid var(--sel-border);}
  .mtile.cur .mn{font-weight:600;}
  .totbar{display:flex; justify-content:space-between; font-size:12px; margin-top:10px;
    padding-top:8px; border-top:0.5px solid var(--line);}
  .totbar .d{color:var(--dn);} .totbar .u{color:var(--up);}

  .btnrow{display:flex; gap:10px;}
  .btn{flex:1; padding:10px; border-radius:12px; background:var(--card);
    border:0.5px solid rgba(0,0,0,0.12); color:var(--txt); font-size:13px;
    display:flex; align-items:center; justify-content:center; gap:6px; cursor:pointer;
    font-family:inherit;}
  .btn:hover{background:#fff;}
  .btn.quit{background:rgba(224,96,63,0.12); border-color:rgba(224,96,63,0.3); color:#c0402a;}
  .ic{font-size:15px; line-height:1;}
  .foot{text-align:center; color:var(--txt-faint); font-size:10px; margin-top:8px; padding-bottom:2px;}

  /* settings */
  .shead{display:flex; align-items:center; gap:8px; margin-bottom:4px;}
  .shead .back{cursor:pointer; font-size:18px; color:var(--txt-dim);}
  .shead .ttl{font-size:15px; font-weight:500;}
  .srow{display:flex; align-items:center; justify-content:space-between; padding:9px 0;
    border-bottom:0.5px solid var(--line); font-size:13px;}
  .srow:last-child{border-bottom:none;}
  .sw{width:42px; height:25px; border-radius:13px; background:rgba(120,120,120,0.35);
    position:relative; cursor:pointer; transition:background .15s;}
  .sw .kn{position:absolute; top:2.5px; left:2.5px; width:20px; height:20px;
    border-radius:50%; background:#fff; transition:left .15s; box-shadow:0 1px 2px rgba(0,0,0,.2);}
  .sw.on{background:var(--blue);}
  .sw.on .kn{left:19.5px;}
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
  .etab.on{background:#fff; color:var(--txt); border:0.5px solid var(--sel-border);}
  .expsel{width:100%; font-size:13px; padding:7px 8px; border-radius:8px;
    border:0.5px solid rgba(0,0,0,0.2); background:#fff; color:var(--txt); margin-bottom:10px;}
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
function speed(bps){ let k=bps/1024; if(k<1024) return k.toFixed(1)+" KB/s"; return (k/1024).toFixed(2)+" MB/s"; }
function dkey(d){ return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); }
function get(k){ return DATA.daily[k]||[0,0]; }

let tab="day";
let cur=new Date(DATA.today+"T12:00:00");
let sel=DATA.today;
let yr=new Date(DATA.today+"T12:00:00").getFullYear();
let view="main";  // "main" | "settings"

function applyColors(){
  const s=DATA.settings;
  document.documentElement.style.setProperty('--dn', s.down_color||'#2e9e5b');
  document.documentElement.style.setProperty('--up', s.up_color||'#e0603f');
  // The outer frosted background is the native vibrancy layer. The slider
  // controls how solid the CARDS are on top of it: 0 = cards nearly clear
  // (wallpaper/frost shows through), 100 = cards solid white.
  const t=Math.max(0,Math.min(100,s.transparency==null?60:s.transparency))/100;
  const cardA=(0.25+0.72*t).toFixed(3);   // 0.25 .. 0.97
  document.documentElement.style.setProperty('--card-alpha', cardA);
  document.documentElement.style.setProperty('--card', 'rgba(255,255,255,'+cardA+')');
  const cd=document.getElementById("col-dn"); if(cd) cd.style.background=s.down_color;
  const cu=document.getElementById("col-up"); if(cu) cu.style.background=s.up_color;
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
    +'<button data-t="year" class="'+(tab==="year"?"active":"")+'">Year</button></div>';
  h+='<div class="nav"><button id="prev">&lsaquo;</button><div class="lbl" id="navlabel"></div><button id="next">&rsaquo;</button></div>';
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

  if(tab==="day") renderDay(); else renderYear();
}

function step(d){ if(tab==="day"){cur.setDate(cur.getDate()+7*d);} else {yr+=d;}
  if(tab==="day") renderDay(); else renderYear(); }

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
  let h='<div class="months">';
  for(let m=1;m<=12;m++){ const t=mtot[m]; const has=!!t;
    const cls="mtile"+(m===curMonth?" cur":"")+(has?"":" empty");
    const v=has?('<div class="mv"><span class="d">↓'+short(t[0])+'</span> <span class="u">↑'+short(t[1])+'</span></div>')
      :'<div class="mv">—</div>';
    h+='<div class="'+cls+'"><div class="mn">'+MONS[m-1]+'</div>'+v+'</div>';
  }
  h+='</div>';
  document.getElementById("calbody").innerHTML=h;
  // year: big card shows the year total, net card shows year networks
  document.getElementById("bigcard").innerHTML=
    '<div class="when">'+yr+'</div><div class="tot">'+human(ytd+ytu)+'</div>'
    +'<div class="sub"><span class="d">↓ '+human(ytd)+'</span> &nbsp; <span class="u">↑ '+human(ytu)+'</span></div>';
  // aggregate networks for the year
  const nagg={};
  for(const k in DATA.networks){ if(k.slice(0,4)!=String(yr)) continue;
    for(const [nm,d,u] of DATA.networks[k]){ if(!nagg[nm])nagg[nm]=[0,0]; nagg[nm][0]+=d; nagg[nm][1]+=u; } }
  const list=Object.entries(nagg).map(([nm,v])=>[nm,v[0],v[1]]).sort((a,b)=>(b[1]+b[2])-(a[1]+a[2]));
  document.getElementById("netcard").innerHTML=netTableHTML(list);
}

function renderSettings(){
  const s=DATA.settings;
  let h='<div class="shead"><span class="back" id="back">‹</span><span class="ttl">Settings</span></div>';
  h+='<div class="card">'
    +'<div class="srow"><span>Tracking</span><div class="sw '+(s.tracking?"on":"")+'" id="sw-track"><div class="kn"></div></div></div>'
    +'<div class="srow"><span>Show number in menu bar</span><div class="sw '+(s.show_menu_text?"on":"")+'" id="sw-menu"><div class="kn"></div></div></div>'
    +'<div class="srow"><span>Launch at login</span><div class="sw '+(s.launch_at_login?"on":"")+'" id="sw-login"><div class="kn"></div></div></div>'
    +'<div class="srow"><span>Transparency</span><input type="range" min="0" max="100" value="'+(s.transparency==null?60:s.transparency)+'" id="rng-trans"></div>'
    +'<div class="srow"><span>Download colour</span><div class="cpick" id="col-dn" style="background:'+s.down_color+'"></div></div>'
    +'<div class="srow"><span>Upload colour</span><div class="cpick" id="col-up" style="background:'+s.up_color+'"></div></div>'
    +'</div>';
  h+='<div class="card" id="export-card">'+exportHTML()+'</div>';
  h+='<div class="card" style="padding:4px 12px;">'
    +'<div class="sitem" id="it-folder"><i class="ti">🗀</i><span>Open data folder</span></div>'
    +'</div>';
  document.getElementById("root").innerHTML=h;
  document.getElementById("foot").textContent="NUM v"+DATA.version+" · by "+DATA.author;

  document.getElementById("back").onclick=()=>{view="main"; render(); requestResize();};
  document.getElementById("sw-track").onclick=()=>toggle("tracking","sw-track");
  document.getElementById("sw-menu").onclick=()=>toggle("show_menu_text","sw-menu");
  document.getElementById("sw-login").onclick=()=>toggle("launch_at_login","sw-login");
  document.getElementById("rng-trans").oninput=(e)=>{ DATA.settings.transparency=parseInt(e.target.value,10);
    applyColors(); bridge("set:transparency:"+DATA.settings.transparency); };
  document.getElementById("col-dn").onclick=()=>bridge("pickcolor:down");
  document.getElementById("col-up").onclick=()=>bridge("pickcolor:up");
  document.getElementById("it-folder").onclick=()=>bridge("folder");
  wireExport();
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

function render(){ applyColors(); if(view==="settings") renderSettings(); else renderMain(); }

// Live update from native without losing view/tab/selection
window.__updateData=function(d){
  try{
    if(d.daily) DATA.daily=d.daily;
    if(d.networks) DATA.networks=d.networks;
    if(d.net!==undefined) DATA.net=d.net;
    if(d.downBps!==undefined) DATA.downBps=d.downBps;
    if(d.upBps!==undefined) DATA.upBps=d.upBps;
    if(d.generated) DATA.generated=d.generated;
    if(view==="main") render();
  }catch(e){}
};

render();
</script>
</body></html>"""
