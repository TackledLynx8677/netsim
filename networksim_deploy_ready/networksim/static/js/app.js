var NS = window.NS || (window.NS = {});

NS.api = (function () {
  async function req(url, opts) {
    opts = opts || {};
    // tell the server which socket made this request so it doesn't echo the change back to us
    opts.headers = Object.assign({}, opts.headers, { "X-Socket-Id": (NS.collab && NS.collab.sid()) || "", "X-Room": NS.room.id });
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }
  const json = (obj) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) });

  return {
    getTopology: () => req("/api/topology"),
    addDevice: (kind, name, x, y) => req("/api/devices", json({ kind, name, x, y })),
    moveDevice: (id, x, y) => req(`/api/devices/${id}/position`, { ...json({ x, y }), method: "PUT" }),
    renameDevice: (id, name) => req(`/api/devices/${id}/rename`, { ...json({ name }), method: "PUT" }),
    deleteDevice: (id) => req(`/api/devices/${id}`, { method: "DELETE" }),
    getDevice: (id) => req(`/api/devices/${id}`),
    addLink: (a_id, b_id) => req("/api/links", json({ a_id, b_id })),
    deleteLink: (id) => req(`/api/links/${id}`, { method: "DELETE" }),
    runCli: (id, command) => req(`/api/cli/${id}`, json({ command })),
    cliPrompt: (id) => req(`/api/cli/${id}/prompt`),
    shareRoom: () => req("/api/room/share", { method: "POST" }),
    unshareRoom: () => req("/api/room/unshare", { method: "POST" }),
    resolveRoom: (code) => req("/api/room/resolve?code=" + encodeURIComponent(code)),
    simulateDhcp: (pc_id) => req("/api/simulate/dhcp", json({ pc_id })),
    simulatePing: (src_id, dst_ip) => req("/api/simulate/ping", json({ src_id, dst_ip })),
    ripConverge: () => req("/api/rip/converge", { method: "POST" }),
    loadDemo: () => req("/api/demo", { method: "POST" }),
    resetTopology: () => req("/api/reset", { method: "POST" }),
  };
})();

NS.app = (function () {
  const statusText = document.getElementById("statusText");
  const statusCounts = document.getElementById("statusCounts");
  const deviceEmpty = document.getElementById("deviceEmpty");
  const deviceDetail = document.getElementById("deviceDetail");
  const linkModeBtn = document.getElementById("linkModeBtn");
  const deleteBtn = document.getElementById("deleteBtn");

  let placeCascade = 0;

  function setStatus(text, isError) {
    statusText.textContent = text;
    statusText.style.color = isError ? "var(--status-down)" : "var(--text-dim)";
    if (!isError) setTimeout(() => { if (statusText.textContent === text) statusText.textContent = "Ready."; }, 3500);
  }

  function setMode(mode) {
    NS.canvas.setMode(mode);
    linkModeBtn.classList.toggle("active", mode === "link");
    deleteBtn.classList.toggle("active", mode === "delete");
  }

  function applyTopology(t) {
    NS.canvas.setTopology(t);
    NS.simulate.populateSourceOptions(t);
    updateCounts(t);
    const sel = NS.canvas.selectedDeviceId;
    const typing = document.activeElement && document.activeElement.id === "renameInput";
    if (sel && t.nodes[sel]) { if (!typing) renderDeviceDetail(t.nodes[sel]); }
    else if (sel && !t.nodes[sel]) onSelectDevice(null);
  }

  async function refresh() {
    const t = await NS.api.getTopology();
    applyTopology(t);
    return t;
  }

  // a change made by another user arrived over the socket
  function applyRemoteTopology(t) {
    const dragId = NS.canvas.draggingId;
    const mine = dragId && NS.canvas.getTopology().nodes[dragId];
    if (mine && t.nodes[dragId]) { t.nodes[dragId].x = mine.x; t.nodes[dragId].y = mine.y; }  // keep my in-progress drag
    applyTopology(t);
  }
  const refreshQuiet = refresh;

  function updateCounts(t) {
    const n = Object.keys(t.nodes).length, l = Object.keys(t.links).length;
    statusCounts.textContent = `${n} device${n === 1 ? "" : "s"} · ${l} link${l === 1 ? "" : "s"}`;
  }

  function onTopologyRendered() { /* hook for future use */ }

  function onSelectDevice(devId) {
    if (!devId) {
      deviceEmpty.classList.remove("hidden");
      deviceDetail.classList.add("hidden");
      NS.cliPanel.close();
      return;
    }
    const t = NS.canvas.getTopology();
    const dev = t.nodes[devId];
    if (!dev) return;
    renderDeviceDetail(dev);
    NS.cliPanel.open(dev);
    NS.simulate.setSource(devId);
  }

  function kindBadge(dev) {
    return `<span class="device-kind-badge ${dev.type}">${dev.type}</span>`;
  }

  function renderDeviceDetail(dev) {
    deviceEmpty.classList.add("hidden");
    deviceDetail.classList.remove("hidden");

    let html = `
      <div class="device-title-row">
        <input class="rename-input" id="renameInput" value="${escapeAttr(dev.name)}" />
        ${kindBadge(dev)}
      </div>
      <div class="dim" style="font-size:11px;margin-bottom:14px;">ID ${dev.id}</div>
    `;

    html += `<div class="detail-block"><div class="detail-heading">Interfaces</div>`;
    Object.values(dev.interfaces).forEach((iface) => {
      const up = iface.status === "up";
      html += `
        <div class="iface-row">
          <span class="iface-dot ${up ? "up" : "down"}"></span>
          <span class="iface-name">${shortIface(iface.name)}</span>
          <span class="iface-ip">${iface.ip ? `${iface.ip} / ${maskToCidr(iface.mask)}` : "unassigned"}<br><span class="dim">${iface.mac}</span></span>
          <span class="pill ${up ? "up" : "down"}">${iface.status}</span>
        </div>`;
    });
    html += `</div>`;

    if (dev.type === "pc" || dev.type === "server") {
      html += `<div class="detail-block"><div class="detail-heading">Default gateway</div>
        <div class="mono">${dev.default_gateway || "<span class=\"dim\">unassigned</span>"}</div></div>`;
    }

    if (dev.type === "pc") {
      html += `<div class="detail-block"><div class="detail-heading">Addressing
        <span class="pill ${dev.dhcp_server ? "up" : "down"}">${dev.dhcp_server ? "DHCP" : "static"}</span></div>
        <button id="dhcpRequestBtn" class="primary-btn">Request IP via DHCP</button></div>`;
    }

    if (dev.type === "server") {
      const d = dev.dhcp || {};
      html += `<div class="detail-block"><div class="detail-heading">DHCP service
        <span class="pill ${d.enabled ? "up" : "down"}">${d.enabled ? "enabled" : "disabled"}</span></div>`;
      if (!d.enabled) {
        html += `<div class="dim">Open the Console tab and run:<br><span class="mono">dhcp pool &lt;first&gt; &lt;last&gt; &lt;mask&gt; &lt;gateway&gt;</span></div>`;
      } else {
        html += `<div class="mono">Pool ${d.pool_start} &ndash; ${d.pool_end}<br>Mask ${d.mask}<br>Gateway ${d.gateway}</div>`;
        const leases = Object.entries(d.leases || {});
        if (leases.length === 0) html += `<div class="dim" style="margin-top:6px;">No leases yet.</div>`;
        else {
          html += `<table class="data-table" style="margin-top:8px;"><thead><tr><th>Leased IP</th><th>Client MAC</th></tr></thead><tbody>`;
          leases.forEach(([mac, ip]) => { html += `<tr><td>${ip}</td><td>${mac}</td></tr>`; });
          html += `</tbody></table>`;
        }
      }
      html += `</div>`;
    }

    if (dev.type === "router") {
      html += `<div class="detail-block"><div class="detail-heading">Routing table
        ${dev.rip_enabled ? '<span class="pill up">RIP on</span>' : ""}</div>`;
      if (dev.routing_table.length === 0) {
        html += `<div class="dim">No routes.</div>`;
      } else {
        html += `<table class="data-table"><thead><tr><th>Code</th><th>Network</th><th>Next hop / iface</th><th>Metric</th></tr></thead><tbody>`;
        dev.routing_table.forEach((r) => {
          html += `<tr><td>${r.source}</td><td>${r.network}</td><td>${r.source === "C" ? shortIface(r.iface) : (r.next_hop || "-")}</td><td>${r.metric}</td></tr>`;
        });
        html += `</tbody></table>`;
      }
      html += `</div>`;
    }

    if (dev.type === "router" || dev.type === "pc" || dev.type === "server") {
      html += `<div class="detail-block"><div class="detail-heading">ARP cache</div>`;
      const entries = Object.entries(dev.arp_table || {});
      if (entries.length === 0) html += `<div class="dim">No entries yet — send a ping to populate it.</div>`;
      else {
        html += `<table class="data-table"><thead><tr><th>IP address</th><th>MAC address</th></tr></thead><tbody>`;
        entries.forEach(([ip, e]) => { html += `<tr><td>${ip}</td><td>${e.mac}</td></tr>`; });
        html += `</tbody></table>`;
      }
      html += `</div>`;
    }

    if (dev.type === "switch") {
      html += `<div class="detail-block"><div class="detail-heading">MAC address table (CAM)</div>`;
      const entries = Object.entries(dev.mac_table || {});
      if (entries.length === 0) html += `<div class="dim">No entries yet — send traffic through this switch to populate it.</div>`;
      else {
        html += `<table class="data-table"><thead><tr><th>MAC address</th><th>Port</th></tr></thead><tbody>`;
        entries.forEach(([mac, port]) => { html += `<tr><td>${mac}</td><td>${shortIface(port)}</td></tr>`; });
        html += `</tbody></table>`;
      }
      html += `</div>`;
    }

    deviceDetail.innerHTML = html;
    const dhcpBtn = document.getElementById("dhcpRequestBtn");
    if (dhcpBtn) dhcpBtn.addEventListener("click", () => NS.simulate.runDhcp(dev.id));
    const renameInput = document.getElementById("renameInput");
    renameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") renameInput.blur(); });
    renameInput.addEventListener("blur", async () => {
      const val = renameInput.value.trim();
      if (val && val !== dev.name) {
        await NS.api.renameDevice(dev.id, val);
        refresh();
      }
    });
  }

  function shortIface(name) {
    if (!name) return "-";
    return name.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa");
  }
  function maskToCidr(mask) {
    if (!mask) return "";
    const bits = mask.split(".").reduce((acc, o) => acc + parseInt(o, 10).toString(2).split("1").length - 1, 0);
    return `/${bits} (${mask})`;
  }
  function escapeAttr(s) { return String(s).replace(/"/g, "&quot;"); }

  //  toolbar 
  document.querySelectorAll(".tool-btn[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      try {
        if (action === "add-router" || action === "add-switch" || action === "add-pc" || action === "add-server") {
          const kind = action.replace("add-", "");
          const pos = nextCascadePosition();
          const dev = await NS.api.addDevice(kind, null, pos.x, pos.y);
          await refresh();
          selectDeviceProgrammatically(dev.id);
          setStatus(`${dev.name} added.`);
        } else if (action === "link-mode") {
          setMode(linkModeBtn.classList.contains("active") ? "select" : "link");
        } else if (action === "delete-selected") {
          setMode(deleteBtn.classList.contains("active") ? "select" : "delete");
        } else if (action === "load-demo") {
          await NS.api.loadDemo();
          setMode("select");
          selectDeviceProgrammatically(null);
          await refresh();
          setStatus("Demo topology loaded.");
        } else if (action === "clear-all") {
          if (confirm("Clear the entire topology? This can't be undone.")) {
            await NS.api.resetTopology();
            setMode("select");
            selectDeviceProgrammatically(null);
            await refresh();
            setStatus("Canvas cleared.");
          }
        }
      } catch (e) {
        setStatus(e.message, true);
      }
    });
  });

  function nextCascadePosition() {
    placeCascade = (placeCascade + 1) % 8;
    return { x: 220 + placeCascade * 46, y: 160 + placeCascade * 34 };
  }

  function selectDeviceProgrammatically(devId) {
    NS.canvas.selectDevice(devId);
    onSelectDevice(devId);
  }

  // tabs 
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "cli") NS.cliPanel.focusInput();
    });
  });

  // init 
  async function init() {
    try {
      await refresh();
      setStatus("Ready.");
    } catch (e) {
      setStatus("Could not reach the server: " + e.message, true);
    }
  }
  init();

  return { setStatus, setMode, refresh, refreshQuiet, applyRemoteTopology, onSelectDevice, onTopologyRendered };
})();
