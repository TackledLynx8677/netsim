var NS = window.NS || (window.NS = {});

NS.room = (function () {
  const mem = {};
  function get(store, k) { try { return window[store].getItem(k); } catch (e) { return mem[store + k] || null; } }
  function set(store, k, v) { try { window[store].setItem(k, v); } catch (e) { mem[store + k] = v; } }
  function del(store, k) { try { window[store].removeItem(k); } catch (e) { delete mem[store + k]; } }
  function randomId() {
    const a = new Uint8Array(16); (window.crypto || window.msCrypto).getRandomValues(a);
    return "p" + Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  let priv = get("localStorage", "netsim-private");
  if (!priv) { priv = randomId(); set("localStorage", "netsim-private", priv); }
  const current = get("sessionStorage", "netsim-room") || priv;
  return {
    id: current,
    isPrivate: current === priv,
    enter(roomId) { set("sessionStorage", "netsim-room", roomId); location.replace(location.pathname); },
    leave() { del("sessionStorage", "netsim-room"); location.replace(location.pathname); },
  };
})();

NS.collab = (function () {
  const header = () => ({ "X-Room": NS.room.id });

  const joinParam = new URLSearchParams(location.search).get("join");
  if (joinParam) {
    fetch("/api/room/resolve?code=" + encodeURIComponent(joinParam), { headers: header() })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (ok && confirm(`You were invited to a shared NetSim room (code ${joinParam.toUpperCase()}).\n\nJoin it and work together? Choose Cancel to stay on your own private canvas.`)) NS.room.enter(j.room);
        else { if (!ok) alert(j.error || "Invalid code."); location.replace(location.pathname); }
      })
      .catch(() => location.replace(location.pathname));
    return { sid: () => "", room: () => NS.room.id, sendCursor() {}, sendDrag() {} };
  }
  if (typeof io === "undefined") {   // socket.io client failed to load: single-player still works
    return { sid: () => "", room: () => NS.room.id, sendCursor() {}, sendDrag() {} };
  }
  const socket = io({ query: { room: NS.room.id } });
  const svg = document.getElementById("canvas");
  const cursorLayer = document.getElementById("cursorLayer");
  const presenceEl = document.getElementById("presence");
  const NSVG = "http://www.w3.org/2000/svg";

  let me = null;
  let panel = null;
  let users = [];
  const cursors = {};         // user id -> <g>
  const pendingDrag = {};    // throttle state
  let lastCursorSent = 0, lastDragSent = 0;

  function savedName() { try { return localStorage.getItem("netsim-name"); } catch (e) { return null; } }
  function saveName(n) { try { localStorage.setItem("netsim-name", n); } catch (e) { /* ignore */ } }

  //  presence 
  function renderPresence() {
    if (panel) panel.remove();   // keep an open share panel alive across presence updates
    presenceEl.innerHTML = "";
    const alone = users.length <= 1;
    const btn = document.createElement("button");
    btn.className = "presence-share";
    btn.textContent = alone ? "\u{1F512} Private \u00B7 Share" : `\u{1F465} Shared (${users.length}) \u00B7 Invite`;
    btn.addEventListener("click", togglePanel);
    presenceEl.appendChild(btn);
    if (!alone) users.forEach((u) => {
      const chip = document.createElement("span");
      chip.className = "presence-chip" + (me && u.id === me.id ? " me" : "");
      chip.title = me && u.id === me.id ? "You - click to change your name" : u.name;
      chip.innerHTML = `<span class="presence-dot" style="background:${u.color}"></span>${escapeHtml(u.name)}${me && u.id === me.id ? " (you)" : ""}`;
      if (me && u.id === me.id) chip.addEventListener("click", () => {
        const n = prompt("Your display name:", me.name);
        if (n && n.trim()) { saveName(n.trim().slice(0, 20)); socket.emit("set_name", { name: n.trim() }); }
      });
      presenceEl.appendChild(chip);
    });
    if (panel) presenceEl.appendChild(panel);
    // drop cursors of users who left
    Object.keys(cursors).forEach((id) => { if (!users.find((u) => u.id === id)) removeCursor(id); });
  }
  function escapeHtml(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  //  share / join panel 
  function togglePanel() {
    if (panel) { panel.remove(); panel = null; return; }
    panel = document.createElement("div");
    panel.className = "share-panel";
    const inShared = !NS.room.isPrivate;
    panel.innerHTML = `
      <div class="share-title">${users.length <= 1 ? "You are working alone" : "You are working with " + (users.length - 1) + " other" + (users.length > 2 ? "s" : "")}</div>
      <div class="share-text">${inShared ? "This is a shared room." : "This canvas is private. Nobody else can see it unless you invite them."}</div>
      <div id="shareResult"></div>
      <button class="primary-btn" id="shareMake">Invite others (get a code)</button>
      <div class="share-sep"></div>
      <div class="share-text">Have a code from someone?</div>
      <div class="share-row"><input id="joinInput" maxlength="6" placeholder="ABC123" autocomplete="off"><button class="tool-btn" id="joinGo">Join</button></div>
      <div id="joinMsg" class="share-msg"></div>
      ${inShared ? '<div class="share-sep"></div><button class="tool-btn ghost" id="leaveRoom">Leave room (back to my private canvas)</button>' : ""}`;
    presenceEl.appendChild(panel);
    panel.querySelector("#shareMake").addEventListener("click", makeCode);
    panel.querySelector("#joinGo").addEventListener("click", joinCode);
    panel.querySelector("#joinInput").addEventListener("keydown", (e) => { if (e.key === "Enter") joinCode(); });
    const lv = panel.querySelector("#leaveRoom"); if (lv) lv.addEventListener("click", () => NS.room.leave());
  }
  async function makeCode() {
    const out = panel.querySelector("#shareResult");
    try {
      const r = await NS.api.shareRoom();
      const link = `${location.origin}${location.pathname}?join=${r.code}`;
      out.innerHTML = `<div class="share-code">${r.code}</div>
        <div class="share-text">Send this code, or the link:</div>
        <div class="share-row"><input id="shareLink" readonly value="${link}"><button class="tool-btn" id="copyLink">Copy</button></div>
        <button class="tool-btn ghost" id="stopShare">Stop sharing this code</button>`;
      out.querySelector("#copyLink").addEventListener("click", () => {
        const el = out.querySelector("#shareLink"); el.select();
        try { navigator.clipboard.writeText(link); } catch (e) { document.execCommand("copy"); }
        out.querySelector("#copyLink").textContent = "Copied";
      });
      out.querySelector("#stopShare").addEventListener("click", async () => {
        await NS.api.unshareRoom(); out.innerHTML = `<div class="share-text">Code disabled. People already in the room stay, but nobody new can join.</div>`;
      });
    } catch (e) { out.textContent = e.message; }
  }
  async function joinCode() {
    const code = panel.querySelector("#joinInput").value.trim();
    const msg = panel.querySelector("#joinMsg");
    if (!code) return;
    try {
      const r = await NS.api.resolveRoom(code);
      NS.room.enter(r.room);
    } catch (e) { msg.textContent = e.message; }
  }

  socket.on("welcome", (d) => {
    me = d.you; users = d.users; renderPresence();
    const n = savedName();
    if (n && n !== me.name) socket.emit("set_name", { name: n });
  });
  socket.on("presence", (d) => {
    users = d.users;
    if (me) { const mine = users.find((u) => u.id === me.id); if (mine) me = mine; }
    renderPresence();
  });
  socket.on("connect", () => presenceEl.classList.remove("offline"));
  socket.on("disconnect", () => { presenceEl.classList.add("offline"); NS.app.setStatus("Disconnected from the collaboration server - reconnecting...", true); });
  socket.io.on("reconnect", () => { NS.app.refresh(); NS.app.setStatus("Reconnected."); });

  //  cursors 
  function svgPointFromEvent(evt) {
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX; pt.y = evt.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }
  svg.addEventListener("mousemove", (e) => {
    const now = performance.now();
    if (now - lastCursorSent < 40) return;       // ~25 updates/second max
    lastCursorSent = now;
    const p = svgPointFromEvent(e);
    socket.emit("cursor", { x: Math.round(p.x), y: Math.round(p.y) });
  });

  function cursorFor(id) {
    if (cursors[id]) return cursors[id];
    const u = users.find((x) => x.id === id) || { name: "?", color: "#999" };
    const g = document.createElementNS(NSVG, "g");
    g.setAttribute("class", "remote-cursor");
    g.innerHTML = `<path d="M0,0 L0,16 L4.5,12 L8,19 L10.5,18 L7,11 L13,11 Z" fill="${u.color}" stroke="#0b1220" stroke-width="1"/>
      <rect x="14" y="14" rx="3" height="15" width="${Math.max(30, u.name.length * 7 + 10)}" fill="${u.color}"/>
      <text x="19" y="25" class="remote-cursor-label">${escapeHtml(u.name)}</text>`;
    cursorLayer.appendChild(g);
    cursors[id] = g;
    return g;
  }
  function removeCursor(id) { if (cursors[id]) { cursors[id].remove(); delete cursors[id]; } }
  socket.on("cursor", (d) => {
    const g = cursorFor(d.id);
    g.setAttribute("transform", `translate(${d.x},${d.y})`);
  });
  socket.on("cursor_gone", (d) => removeCursor(d.id));

  //  live drag 
  socket.on("drag", (d) => {
    const t = NS.canvas.getTopology();
    const dev = t && t.nodes[d.id];
    if (!dev || NS.canvas.draggingId === d.id) return;
    dev.x = d.x; dev.y = d.y;
    NS.canvas.render();
  });
  function sendDrag(id, x, y) {
    const now = performance.now();
    if (now - lastDragSent < 30) return;
    lastDragSent = now;
    socket.emit("drag", { id, x: Math.round(x), y: Math.round(y) });
  }

  //  changes made by other users 
  socket.on("topology", (d) => {
    NS.app.applyRemoteTopology(d.topology);
    if (d.activity) NS.app.setStatus(`${d.by} ${d.activity}.`);
  });
  socket.on("sim_play", (d) => NS.simulate.playRemote(d));

  return { sid: () => (socket && socket.id) || "", room: () => NS.room.id, sendCursor() {}, sendDrag };
})();
