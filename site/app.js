/* Aeronex landing: animated sky, live public snapshot, scroll reveals. No dependencies. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const CATS = [ // CPCB AQI bands
    { max: 50, name: "Good", c: "#00b050" }, { max: 100, name: "Satisfactory", c: "#92d050" },
    { max: 200, name: "Moderate", c: "#ffc000" }, { max: 300, name: "Poor", c: "#ff7c00" },
    { max: 400, name: "Very Poor", c: "#ff0000" }, { max: 9999, name: "Severe", c: "#a00000" },
  ];
  const cat = (v) => CATS.find((x) => v <= x.max);

  /* ---------- session: ONE sign-in shared by the website and the dashboard (HttpOnly cookie set by /v1/auth/login) ---------- */
  const dashboardUrl = "/app/";            // same origin: the backend serves the dashboard here
  let me = null;                           // {username, display_name, role} when signed in
  const navAuth = $("#navAuth"), modal = $("#loginModal");
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function renderAuth() {
    if (me) {
      const role = me.role === "admin" ? "Official" : "Public";
      navAuth.innerHTML = `<a class="btn btn-primary" href="${dashboardUrl}">Dashboard</a>` +
        `<span class="nav-chip"><i></i>${role} &middot; ${esc(me.display_name)}</span><button class="btn-out" id="btnOut" type="button">Sign out</button>`;
      $("#btnOut").addEventListener("click", signOut);
    } else {
      navAuth.innerHTML = `<a class="btn btn-primary nav-cta" href="#" data-login>Login to view stats</a>`;
    }
    $$("[data-login]").forEach((el) => {
      if (el.dataset.login === "signup") { el.hidden = !!me; return; }
      if (el.id === "mobAuth") { el.textContent = me ? "Open dashboard" : "Login"; return; }
      if (!el.dataset.label) el.dataset.label = el.textContent.trim();
      if (me) el.childNodes[0].nodeValue = "Open dashboard "; else if (el.dataset.label) el.childNodes[0].nodeValue = el.dataset.label + " ";
    });
  }
  async function loadMe() {
    try { const r = await fetch("/v1/auth/me", { cache: "no-store" }); me = r.ok ? await r.json() : null; } catch { me = null; }
    renderAuth();
  }
  async function signOut() {
    try { await fetch("/v1/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    me = null; renderAuth();
  }

  /* login modal */
  let tab = "in";
  const setTab = (t) => {
    tab = t;
    $$(".tab", modal).forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    $("#formIn").hidden = t !== "in"; $("#formUp").hidden = t !== "up";
    $("#lmTitle").textContent = t === "in" ? "Sign in to view stats" : "Create a public account";
    $("#inErr").textContent = $("#upErr").textContent = "";
  };
  function openLogin(t = "in") {
    setTab(t); modal.hidden = false; document.body.style.overflow = "hidden";
    setTimeout(() => $(t === "in" ? "#inUser" : "#upUser").focus(), 30);
  }
  function closeLogin() { modal.hidden = true; document.body.style.overflow = ""; }
  $$(".tab", modal).forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $("#lmClose").addEventListener("click", closeLogin);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeLogin(); });
  addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.hidden) closeLogin(); });

  async function doLogin(username, password, errEl) {
    errEl.textContent = "";
    let r, body = {};
    try {
      r = await fetch("/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
      body = await r.json().catch(() => ({}));
    } catch { errEl.textContent = "Cannot reach the server."; return false; }
    if (!r.ok) { errEl.textContent = typeof body.detail === "string" ? body.detail : "Sign-in failed."; return false; }
    me = body.user; renderAuth();
    window.location.href = dashboardUrl;          // signed in: straight to the dashboard (Home is one click away in its navbar)
    return true;
  }
  $("#formIn").addEventListener("submit", (e) => { e.preventDefault(); doLogin($("#inUser").value.trim(), $("#inPass").value, $("#inErr")); });
  $("#formUp").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#upErr"); err.textContent = "";
    if ($("#upPass").value !== $("#upPass2").value) { err.textContent = "Passwords do not match."; return; }
    let r, body = {};
    try {
      r = await fetch("/v1/auth/register", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: $("#upUser").value.trim(), password: $("#upPass").value, display_name: $("#upName").value.trim() }) });
      body = await r.json().catch(() => ({}));
    } catch { err.textContent = "Cannot reach the server."; return; }
    if (!r.ok) { err.textContent = typeof body.detail === "string" ? body.detail : (Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : "Could not create the account."); return; }
    await doLogin($("#upUser").value.trim(), $("#upPass").value, err);       // new account: sign straight in
  });

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-login]");
    if (!el) return;
    e.preventDefault();
    links.classList.remove("open");
    if (me) { window.location.href = dashboardUrl; return; }        // already signed in: no second login, just go
    openLogin(el.dataset.login === "signup" ? "up" : "in");
  });
  loadMe().then(() => { if (!me && new URLSearchParams(location.search).get("login")) openLogin("in"); });

  /* ---------- navbar ---------- */
  const nav = $("#nav"), burger = $("#burger"), links = $("#links");
  const onScroll = () => nav.classList.toggle("solid", scrollY > 24);
  addEventListener("scroll", onScroll, { passive: true }); onScroll();
  burger.addEventListener("click", () => {
    const open = links.classList.toggle("open");
    burger.setAttribute("aria-expanded", open);
  });
  links.addEventListener("click", (e) => { if (e.target.tagName === "A") { links.classList.remove("open"); burger.setAttribute("aria-expanded", false); } });
  const secs = $$("main section[id]");
  const spy = new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) $$("a", links).forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id));
  }), { rootMargin: "-45% 0px -50% 0px" });
  secs.forEach((s) => spy.observe(s));

  /* ---------- scroll reveal (staggered inside grids) ---------- */
  $$(".cards,.zones,.steps,.two,.stat-strip").forEach((g) => $$(".reveal", g).forEach((el, i) => el.style.setProperty("--d", i * 0.08 + "s")));
  const rev = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); rev.unobserve(e.target); } }), { threshold: 0.12 });
  $$(".reveal").forEach((el) => (reduce ? el.classList.add("in") : rev.observe(el)));

  /* ---------- animated sky: drifting particles that react to the pointer, tinted by city AQI ---------- */
  const cv = $("#sky"), cx = cv.getContext("2d");
  let W = 0, H = 0, dots = [], tint = [94, 234, 212], mouse = { x: -999, y: -999 };
  const resize = () => {
    const dpr = Math.min(devicePixelRatio || 1, 2), r = cv.getBoundingClientRect();
    W = r.width; H = r.height; cv.width = W * dpr; cv.height = H * dpr; cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.round(Math.min(150, (W * H) / 11000));
    dots = Array.from({ length: n }, () => ({
      x: Math.random() * W, y: Math.random() * H, r: 0.8 + Math.random() * 2.8,
      vx: 0.12 + Math.random() * 0.45, vy: (Math.random() - 0.5) * 0.14, a: 0.25 + Math.random() * 0.5, p: Math.random() * 6.28,
    }));
  };
  addEventListener("resize", resize); resize();
  cv.parentElement.addEventListener("pointermove", (e) => { const r = cv.getBoundingClientRect(); mouse = { x: e.clientX - r.left, y: e.clientY - r.top }; });
  cv.parentElement.addEventListener("pointerleave", () => (mouse = { x: -999, y: -999 }));
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const setTint = (c) => { tint = hex(c); };
  let t0 = 0, visible = true;
  new IntersectionObserver((e) => (visible = e[0].isIntersecting)).observe(cv);
  const frame = (t) => {
    requestAnimationFrame(frame);
    if (!visible) return;
    cx.clearRect(0, 0, W, H);
    const [R, G, B] = tint;
    for (const d of dots) {
      d.x += d.vx; d.y += d.vy + Math.sin(t / 1800 + d.p) * 0.12;
      const dx = d.x - mouse.x, dy = d.y - mouse.y, dist = Math.hypot(dx, dy);
      if (dist < 110) { d.x += (dx / dist) * 1.6; d.y += (dy / dist) * 1.6; }   // particles scatter away from the cursor
      if (d.x > W + 10) { d.x = -10; d.y = Math.random() * H; }
      if (d.y < -10) d.y = H + 10; if (d.y > H + 10) d.y = -10;
      cx.beginPath(); cx.arc(d.x, d.y, d.r, 0, 6.283);
      cx.fillStyle = `rgba(${R},${G},${B},${d.a * (0.7 + 0.3 * Math.sin(t / 900 + d.p))})`; cx.fill();
    }
    for (let i = 0; i < dots.length; i++) for (let j = i + 1; j < dots.length; j++) {  // faint links between near particles
      const a = dots[i], b = dots[j], dd = (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
      if (dd < 8100) { cx.strokeStyle = `rgba(${R},${G},${B},${0.13 * (1 - dd / 8100)})`; cx.lineWidth = 1; cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y); cx.stroke(); }
    }
  };
  if (!reduce) requestAnimationFrame(frame);

  /* ---------- number tween ---------- */
  const tween = (el, to, ms = 1100) => {
    const from = parseFloat(el.dataset.v || "0") || 0; el.dataset.v = to;
    if (reduce || isNaN(to)) { el.textContent = to; return; }
    const s = performance.now();
    const step = (n) => { const k = Math.min(1, (n - s) / ms), e = 1 - Math.pow(1 - k, 3); el.textContent = Math.round(from + (to - from) * e); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  };

  /* ---------- live public snapshot ---------- */
  const zonesEl = $("#zones");
  zonesEl.innerHTML = Array.from({ length: 6 }, () => `<div class="zone skel reveal"><h3>Loading&hellip;</h3><div class="row"><span class="aqi">--</span></div><div class="bar"><i></i></div></div>`).join("");
  $$(".zone", zonesEl).forEach((z, i) => { z.style.setProperty("--d", i * 0.08 + "s"); rev.observe(z); });

  const setGauge = (v) => {
    const arc = $("#gArc"), c = cat(v), frac = Math.min(1, v / 500);
    arc.style.setProperty("--gc", c.c); $(".gauge-card").style.setProperty("--gc", c.c);
    arc.style.strokeDashoffset = 528 * (1 - frac);
    tween($("#gNum"), v); $("#gCat").textContent = c.name; setTint(c.c);
  };

  async function refresh() {
    let s;
    try { s = await (await fetch("/v1/public/summary", { cache: "no-store" })).json(); } catch { return offline(); }
    if (s.city.aqi_avg != null) { setGauge(s.city.aqi_avg); $("#gFoot").textContent = "Average of " + s.zones.filter((z) => z.aqi != null).length + " zones - indicative, prototype sensors"; }
    else { $("#gCat").textContent = "waiting for data"; }
    zonesEl.innerHTML = s.zones.map((z, i) => {
      const c = z.aqi != null ? cat(z.aqi) : null;
      return `<article class="zone reveal in" style="--zc:${c ? c.c : "#cbd5e1"};--d:${i * 0.06}s">
        <h3>${z.name}</h3>
        <div class="row"><span class="aqi" data-v="0" data-t="${z.aqi ?? ""}">${z.aqi ?? "--"}</span>${c ? `<span class="cat">${c.name}</span>` : ""}</div>
        <div class="bar"><i data-w="${z.aqi != null ? Math.min(100, (z.aqi / 500) * 100) : 0}"></i></div>
        <small>${z.pm25 != null ? "PM2.5 ~ " + z.pm25.toFixed(0) + " µg/m³ (smoothed)" : "No reading yet"}</small></article>`;
    }).join("");
    requestAnimationFrame(() => {
      $$(".zone .bar i", zonesEl).forEach((b) => (b.style.width = b.dataset.w + "%"));
      $$(".zone .aqi", zonesEl).forEach((a) => a.dataset.t && tween(a, +a.dataset.t));
    });
    $("#stOnline").textContent = s.network.online + " / " + s.network.devices;
    $("#stWorst").textContent = s.city.worst_zone ? s.city.worst_aqi : "--";
    $("#stWorst").nextElementSibling.textContent = s.city.worst_zone ? "highest: " + s.city.worst_zone.split(" ")[0] : "highest zone right now";
  }
  function offline() { $("#gCat").textContent = "live feed offline"; $("#gFoot").textContent = "Start the backend to see live values"; }
  refresh(); setInterval(refresh, 15000);
})();
