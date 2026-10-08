"""Aeronex look for the Streamlit dashboard: same palette, logo and motion language as the website (site/)."""
from __future__ import annotations

LOGO = (
    '<svg class="ax-logo" viewBox="0 0 48 48" aria-hidden="true"><defs><linearGradient id="axg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#5eead4"/><stop offset="1" stop-color="#38bdf8"/></linearGradient></defs>'
    '<circle cx="24" cy="24" r="21" fill="none" stroke="url(#axg)" stroke-width="3"/>'
    '<path d="M8 27c6 0 6-7 12-7s6 7 12 7 5-4 8-4" fill="none" stroke="url(#axg)" stroke-width="3" stroke-linecap="round"/>'
    '<path d="M12 34c5 0 5-5 10-5s5 5 10 5 4-3 6-3" fill="none" stroke="url(#axg)" stroke-width="2.4" stroke-linecap="round" opacity=".6"/>'
    '<circle cx="33" cy="14" r="2.6" fill="#5eead4"/></svg>'
)

CSS = """
<style>
:root{--ink:#06202b;--ink2:#0b2f3d;--teal:#5eead4;--sky:#38bdf8;--line:rgba(94,234,212,.18)}
html,body,[class*="css"],.stApp{font-family:"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif}
/* hide Streamlit chrome */
#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],
header[data-testid="stHeader"]{display:none!important}
/* background: deep teal sky with slow drifting glow + faint particles */
.stApp{background:radial-gradient(1100px 520px at 80% -5%,#0f4a5c 0%,transparent 60%),linear-gradient(165deg,#04141c 0%,#06202b 50%,#0a3a49 100%);background-attachment:fixed}
.stApp::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.55;
  background-image:radial-gradient(circle,rgba(94,234,212,.5) 1px,transparent 1.6px),radial-gradient(circle,rgba(56,189,248,.4) 1px,transparent 1.6px);
  background-size:120px 120px,190px 190px;background-position:0 0,40px 70px;animation:axdrift 70s linear infinite}
@keyframes axdrift{to{background-position:240px 0,-150px 70px}}
.stApp::after{content:"";position:fixed;width:520px;height:520px;right:-140px;top:40px;border-radius:50%;pointer-events:none;z-index:0;
  background:radial-gradient(circle,rgba(94,234,212,.2),transparent 65%);filter:blur(30px);animation:axbreathe 8s ease-in-out infinite}
@keyframes axbreathe{50%{transform:scale(1.15);opacity:.6}}
[data-testid="stAppViewContainer"],[data-testid="stSidebar"]{position:relative;z-index:1}
.block-container{padding-top:4.6rem!important;max-width:1240px}

/* top navbar (same as the website) */
.ax-nav{position:fixed;top:0;left:0;right:0;height:62px;z-index:100;display:flex;align-items:center;gap:18px;padding:0 26px;
  background:rgba(6,32,43,.86);backdrop-filter:blur(12px);box-shadow:0 6px 24px -10px rgba(0,0,0,.6);border-bottom:1px solid var(--line)}
.ax-brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.2rem;color:#fff!important;text-decoration:none!important;letter-spacing:.2px}
.ax-logo{width:32px;height:32px}
.ax-nav .sp{flex:1}
.ax-nav a.l{color:#cfe6ee!important;text-decoration:none!important;padding:7px 14px;border-radius:999px;font-weight:600;font-size:.92rem;transition:.2s}
.ax-nav a.l:hover{background:rgba(94,234,212,.14);color:#fff!important}
.ax-chip{display:flex;align-items:center;gap:8px;padding:6px 14px;border-radius:999px;background:rgba(94,234,212,.12);border:1px solid rgba(94,234,212,.35);color:#b9f3e8;font-weight:700;font-size:.85rem}
.ax-chip i{width:8px;height:8px;border-radius:50%;background:var(--teal);animation:axping 2s infinite}
@keyframes axping{70%{box-shadow:0 0 0 9px rgba(94,234,212,0)}0%{box-shadow:0 0 0 0 rgba(94,234,212,.7)}}

/* sidebar */
[data-testid="stSidebar"]{background:linear-gradient(190deg,#04141c,#082c38 70%,#0a3a49);border-right:1px solid var(--line)}
[data-testid="stSidebar"] [data-testid="stSidebarContent"]{padding-top:4.2rem}
.ax-side-brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.35rem;margin:-6px 0 4px}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:6px 12px;border-radius:12px;transition:.2s}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:rgba(94,234,212,.12)}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:linear-gradient(135deg,rgba(94,234,212,.22),rgba(56,189,248,.22));box-shadow:inset 0 0 0 1px rgba(94,234,212,.4)}

/* screen menu: pill bar at the top of the page (the sidebar can be collapsed or hidden on small windows, this cannot) */
.st-key-nav_screen{position:sticky;top:62px;z-index:90;width:100%!important;background:linear-gradient(180deg,rgba(6,32,43,.97),rgba(6,32,43,.85));
  backdrop-filter:blur(10px);padding:10px 4px;margin-bottom:.4rem;border-bottom:1px solid var(--line)}
.st-key-nav_screen [role="radiogroup"]{display:flex;flex-wrap:wrap;gap:6px}
.st-key-nav_screen [data-testid="stRadioOption"]{padding:6px 16px;border-radius:999px;transition:.2s;cursor:pointer;margin:0;background:rgba(255,255,255,.04);border:1px solid var(--line)}
.st-key-nav_screen [data-testid="stRadioOption"]:hover{background:rgba(94,234,212,.14)}
.st-key-nav_screen [data-testid="stRadioOption"] > div > div:first-child{display:none!important}
.st-key-nav_screen [data-testid="stRadioOption"][data-selected="true"]{background:linear-gradient(135deg,#5eead4,#38bdf8);border-color:transparent}
.st-key-nav_screen [data-testid="stRadioOption"][data-selected="true"] p{color:#052028!important;font-weight:800}
[data-testid="stSidebarCollapsedControl"],[data-testid="stExpandSidebarButton"]{top:72px!important;z-index:120}

/* headings + cards */
h1,h2,h3{letter-spacing:-.3px}
h1,h2{font-weight:800!important}
.ax-grad{background:linear-gradient(90deg,#5eead4,#38bdf8,#a78bfa,#5eead4);background-size:250% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:axflow 8s linear infinite}
@keyframes axflow{to{background-position:250% 0}}
[data-testid="stVerticalBlockBorderWrapper"]{background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(255,255,255,.025));border:1px solid var(--line)!important;border-radius:18px!important;box-shadow:0 18px 40px -22px rgba(0,0,0,.7);transition:transform .25s,border-color .25s}
[data-testid="stVerticalBlockBorderWrapper"]:hover{border-color:rgba(94,234,212,.45)!important}
[data-testid="stForm"]{background:linear-gradient(160deg,rgba(255,255,255,.08),rgba(255,255,255,.03));border:1px solid var(--line);border-radius:20px;padding:22px 22px 14px;box-shadow:0 24px 50px -24px rgba(0,0,0,.8)}
[data-testid="stExpander"]{border:1px solid var(--line)!important;border-radius:14px!important;background:rgba(255,255,255,.03)}
[data-testid="stDataFrame"]{border-radius:14px;overflow:hidden;border:1px solid var(--line)}
[data-testid="stMetric"]{background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:16px;padding:12px 16px}
[data-testid="stAlert"]{border-radius:14px}
.stProgress>div>div>div>div{background:linear-gradient(90deg,#5eead4,#38bdf8)!important}
[data-baseweb="tab-highlight"]{background:linear-gradient(90deg,#5eead4,#38bdf8)!important}

/* buttons */
.stButton>button,.stFormSubmitButton>button{border-radius:999px;border:1.5px solid rgba(94,234,212,.5);font-weight:700;transition:transform .2s,box-shadow .2s,background .2s}
.stButton>button:hover,.stFormSubmitButton>button:hover{transform:translateY(-2px);border-color:#5eead4;box-shadow:0 10px 24px -10px rgba(56,189,248,.8)}
[data-testid="stBaseButton-primary"],[data-testid="stBaseButton-primaryFormSubmit"],button[kind="primary"],button[kind="primaryFormSubmit"]{
  background:linear-gradient(135deg,#5eead4,#38bdf8)!important;color:#052028!important;border:0!important;box-shadow:0 8px 24px -8px rgba(56,189,248,.7)}

/* sign-in page */
.ax-login{text-align:center;margin:1.2rem 0 1.4rem}
.ax-login .ax-logo{width:64px;height:64px;animation:axfloat 5s ease-in-out infinite}
@keyframes axfloat{50%{transform:translateY(-8px)}}
.ax-login h1{font-size:2.6rem;margin:.4rem 0 .2rem}
.ax-login p{color:#9fc3cf;margin:0}
.ax-back a{color:#5eead4!important;text-decoration:none!important;font-weight:600}
.ax-nav a.cur{background:rgba(94,234,212,.14);color:#fff!important}
.ax-nav a.l.out{border:1.5px solid rgba(230,246,251,.4);padding:6px 14px}
.ax-btn{display:inline-block;padding:10px 22px;margin:0 6px 6px 0;border-radius:999px;font-weight:700;text-decoration:none!important;color:#052028!important;
  background:linear-gradient(135deg,#5eead4,#38bdf8);box-shadow:0 8px 22px -8px rgba(56,189,248,.7);transition:transform .2s}
.ax-btn:hover{transform:translateY(-2px)}
.ax-btn.ghost{background:transparent;color:#e6f6fb!important;border:1.5px solid rgba(230,246,251,.4);box-shadow:none}
.ax-btn.small{padding:6px 14px;font-size:.85rem}
@media (max-width:760px){.ax-nav{padding:0 14px}.ax-nav a.l.hide-sm{display:none}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
</style>
"""


def navbar(user: dict | None, home_url: str) -> str:
    """Same top bar as the website. Home and Sign out are real links (same tab); the cookie keeps you signed in across them."""
    home = home_url if home_url.endswith("/") else home_url + "/"
    right = ""
    if user:
        role = "Official" if user["role"] == "admin" else "Public"
        right = (f'<a class="l hide-sm cur" href="/app/" target="_self">Dashboard</a>'
                 f'<span class="ax-chip"><i></i>{role} &middot; {user["display_name"]}</span>'
                 f'<a class="l out" href="{home}v1/auth/logout?next=/" target="_self">Sign out</a>')
    return (f'<div class="ax-nav"><a class="ax-brand" href="{home}" target="_self">{LOGO}<span>Aeronex</span></a><span class="sp"></span>'
            f'<a class="l hide-sm" href="{home}" target="_self">Home</a>'
            f'<a class="l hide-sm" href="{home}#live" target="_self">Live snapshot</a>'
            f'<a class="l hide-sm" href="{home}#features" target="_self">Features</a>{right}</div>')
