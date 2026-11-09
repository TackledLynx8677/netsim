var NS = window.NS || (window.NS = {});

NS.canvas = (function () {
  const svg = document.getElementById("canvas");
  const linksLayer = document.getElementById("linksLayer");
  const devicesLayer = document.getElementById("devicesLayer");
  const fxLayer = document.getElementById("fxLayer");
  const linkHint = document.getElementById("linkHint");
  const emptyHint = document.getElementById("emptyHint");

  const NSVG = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}) => {
    const e = document.createElementNS(NSVG, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  };

  let topology = { nodes: {}, links: {} };
  let mode = "select"; // select | link | delete
  let pendingLinkDeviceId = null;
  let selectedDeviceId = null;
  let selectedLinkId = null;
  let dragState = null;

  function setTopology(t) {
    topology = t;
    render();
  }

  function getTopology() { return topology; }

  function deviceCenter(devId) {
    const d = topology.nodes[devId];
    if (!d) return { x: 0, y: 0 };
    return { x: d.x, y: d.y };
  }

  //  device glyphs 
  function buildDeviceShape(dev) {
    const g = el("g", { class: `device-group kind-${dev.type}` });
    if (dev.type === "router") {
      g.appendChild(el("rect", { class: "device-body", x: -32, y: -20, width: 64, height: 40, rx: 8 }));
      const glyph = el("g", { class: "device-glyph" });
      glyph.appendChild(el("circle", { cx: -14, cy: 0, r: 3 }));
      glyph.appendChild(el("circle", { cx: 14, cy: 0, r: 3 }));
      glyph.appendChild(el("path", { d: "M -10,-5 Q 0,-14 10,-5", "marker-end": "" }));
      glyph.appendChild(el("path", { d: "M -10,5 Q 0,14 10,5" }));
      glyph.appendChild(el("path", { d: "M 7,-8 L 11,-5 L 7,-2" }));
      glyph.appendChild(el("path", { d: "M -7,2 L -11,5 L -7,8" }));
      g.appendChild(glyph);
    } else if (dev.type === "switch") {
      g.appendChild(el("rect", { class: "device-body", x: -36, y: -18, width: 72, height: 36, rx: 5 }));
      const glyph = el("g", { class: "device-glyph" });
      for (let i = -24; i <= 24; i += 12) glyph.appendChild(el("line", { x1: i, y1: 8, x2: i, y2: 13 }));
      glyph.appendChild(el("line", { x1: -20, y1: -3, x2: 20, y2: -3 }));
      glyph.appendChild(el("path", { d: "M -14,-9 L -20,-3 L -14,3" }));
      glyph.appendChild(el("path", { d: "M 14,-9 L 20,-3 L 14,3" }));
      g.appendChild(glyph);
    } else if (dev.type === "server") {
      g.appendChild(el("rect", { class: "device-body", x: -22, y: -22, width: 44, height: 44, rx: 4 }));
      const glyph = el("g", { class: "device-glyph" });
      glyph.appendChild(el("rect", { x: -15, y: -16, width: 30, height: 12, rx: 1.5 }));
      glyph.appendChild(el("rect", { x: -15, y: 2, width: 30, height: 12, rx: 1.5 }));
      glyph.appendChild(el("line", { x1: -10, y1: -10, x2: -6, y2: -10 }));
      glyph.appendChild(el("line", { x1: -10, y1: 8, x2: -6, y2: 8 }));
      g.appendChild(glyph);
    } else {
      g.appendChild(el("rect", { class: "device-body", x: -24, y: -18, width: 48, height: 30, rx: 3 }));
      const glyph = el("g", { class: "device-glyph" });
      glyph.appendChild(el("rect", { x: -18, y: -13, width: 36, height: 20, rx: 1.5 }));
      glyph.appendChild(el("line", { x1: -6, y1: 12, x2: 6, y2: 12 }));
      glyph.appendChild(el("line", { x1: 0, y1: 7, x2: 0, y2: 12 }));
      g.appendChild(glyph);
    }
    const label = el("text", { class: "device-label", x: 0, y: dev.type === "switch" ? 34 : dev.type === "server" ? 36 : 32 });
    label.textContent = dev.name;
    g.appendChild(label);

    const anyUp = Object.values(dev.interfaces).some((i) => i.status === "up");
    const led = el("circle", {
      class: `status-led ${anyUp ? "up" : "down"}`,
      cx: dev.type === "switch" ? 30 : dev.type === "router" ? 26 : dev.type === "server" ? 22 : 18,
      cy: dev.type === "pc" ? -14 : -14,
    });
    g.appendChild(led);
    return g;
  }

  function ifaceCenterOffset(dev, ifaceName, otherDevId) {
    // Simple radial anchor: point the link toward the other device's direction.
    const other = topology.nodes[otherDevId];
    if (!other) return { x: dev.x, y: dev.y };
    const dx = other.x - dev.x, dy = other.y - dev.y;
    const dist = Math.hypot(dx, dy) || 1;
    const r = dev.type === "switch" ? 34 : dev.type === "router" ? 30 : dev.type === "server" ? 26 : 22;
    return { x: dev.x + (dx / dist) * r, y: dev.y + (dy / dist) * r };
  }

  function render() {
    linksLayer.innerHTML = "";
    devicesLayer.innerHTML = "";
    const hasDevices = Object.keys(topology.nodes).length > 0;
    emptyHint.classList.toggle("hidden", hasDevices);

    // links
    Object.values(topology.links).forEach((link) => {
      const a = topology.nodes[link.a_device], b = topology.nodes[link.b_device];
      if (!a || !b) return;
      const p1 = ifaceCenterOffset(a, link.a_iface, link.b_device);
      const p2 = ifaceCenterOffset(b, link.b_iface, link.a_device);
      const aIface = a.interfaces[link.a_iface], bIface = b.interfaces[link.b_iface];
      const isDown = !(aIface && bIface && aIface.enabled && bIface.enabled);

      const line = el("line", {
        class: `link-line ${isDown ? "down" : ""} ${selectedLinkId === link.id ? "selected" : ""}`,
        x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, "data-link-id": link.id,
      });
      line.addEventListener("click", (e) => { e.stopPropagation(); onLinkClick(link.id); });
      linksLayer.appendChild(line);

      const mid1 = { x: p1.x + (p2.x - p1.x) * 0.22, y: p1.y + (p2.y - p1.y) * 0.22 };
      const mid2 = { x: p1.x + (p2.x - p1.x) * 0.78, y: p1.y + (p2.y - p1.y) * 0.78 };
      const lbl1 = el("text", { class: "link-iface-label", x: mid1.x, y: mid1.y - 5 });
      lbl1.textContent = shortIface(link.a_iface);
      const lbl2 = el("text", { class: "link-iface-label", x: mid2.x, y: mid2.y - 5 });
      lbl2.textContent = shortIface(link.b_iface);
      linksLayer.appendChild(lbl1);
      linksLayer.appendChild(lbl2);
    });

    // devices
    Object.values(topology.nodes).forEach((dev) => {
      const g = buildDeviceShape(dev);
      g.setAttribute("transform", `translate(${dev.x},${dev.y})`);
      g.dataset.deviceId = dev.id;
      if (dev.id === selectedDeviceId) g.classList.add("selected");
      if (dev.id === pendingLinkDeviceId) g.classList.add("link-pending-highlight");
      g.addEventListener("mousedown", (e) => onDeviceMouseDown(e, dev.id));
      g.addEventListener("click", (e) => { e.stopPropagation(); onDeviceClick(dev.id); });
      devicesLayer.appendChild(g);
    });

    NS.app && NS.app.onTopologyRendered && NS.app.onTopologyRendered(topology);
  }

  function shortIface(name) {
    // GigabitEthernet0/0 -> Gi0/0, FastEthernet0/1 -> Fa0/1, FastEthernet0 -> Fa0
    return name.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa");
  }

  //interaction 
  function svgPoint(evt) {
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX; pt.y = evt.clientY;
    const ctm = svg.getScreenCTM();
    return pt.matrixTransform(ctm.inverse());
  }

  function onDeviceMouseDown(e, devId) {
    if (mode !== "select") return;
    e.preventDefault();
    const p = svgPoint(e);
    const dev = topology.nodes[devId];
    dragState = { devId, offsetX: p.x - dev.x, offsetY: p.y - dev.y, moved: false };
  }

  svg.addEventListener("mousemove", (e) => {
    if (!dragState) return;
    const p = svgPoint(e);
    const dev = topology.nodes[dragState.devId];
    dev.x = p.x - dragState.offsetX;
    dev.y = p.y - dragState.offsetY;
    dragState.moved = true;
    NS.collab && NS.collab.sendDrag(dragState.devId, dev.x, dev.y);   // show the move live to other users
    render();
  });

  window.addEventListener("mouseup", () => {
    if (dragState && dragState.moved) {
      const dev = topology.nodes[dragState.devId];
      NS.api.moveDevice(dragState.devId, dev.x, dev.y);
    }
    dragState = null;
  });

  function onDeviceClick(devId) {
    if (mode === "link") {
      if (!pendingLinkDeviceId) {
        pendingLinkDeviceId = devId;
        render();
      } else if (pendingLinkDeviceId === devId) {
        pendingLinkDeviceId = null;
        render();
      } else {
        const a = pendingLinkDeviceId, b = devId;
        pendingLinkDeviceId = null;
        NS.api.addLink(a, b).then(() => NS.app.refresh()).catch((e) => NS.app.setStatus(e.message, true));
      }
      return;
    }
    if (mode === "delete") {
      NS.api.deleteDevice(devId).then(() => {
        if (selectedDeviceId === devId) selectedDeviceId = null;
        NS.app.refresh();
        NS.app.setMode("select");
      });
      return;
    }
    selectedDeviceId = devId;
    selectedLinkId = null;
    render();
    NS.app.onSelectDevice(devId);
  }

  function onLinkClick(linkId) {
    if (mode === "delete") {
      NS.api.deleteLink(linkId).then(() => { NS.app.refresh(); NS.app.setMode("select"); });
      return;
    }
    selectedLinkId = linkId;
    selectedDeviceId = null;
    render();
  }

  svg.addEventListener("click", () => {
    if (mode === "select") {
      selectedDeviceId = null;
      selectedLinkId = null;
      render();
      NS.app.onSelectDevice(null);
    }
  });

  function selectDevice(devId) {
    selectedDeviceId = devId;
    selectedLinkId = null;
    render();
  }

  function setMode(newMode) {
    mode = newMode;
    pendingLinkDeviceId = null;
    linkHint.classList.toggle("hidden", mode !== "link");
    render();
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (mode !== "select") NS.app.setMode("select");
    }
    if ((e.key === "Delete" || e.key === "Backspace") && document.activeElement.tagName !== "INPUT") {
      if (selectedDeviceId) { NS.api.deleteDevice(selectedDeviceId).then(() => { selectedDeviceId = null; NS.app.refresh(); }); }
      else if (selectedLinkId) { NS.api.deleteLink(selectedLinkId).then(() => { selectedLinkId = null; NS.app.refresh(); }); }
    }
  });

  //  packet FX
  function coordsFor(devId) {
    const d = topology.nodes[devId];
    return d ? { x: d.x, y: d.y } : { x: 0, y: 0 };
  }

  function animateFrame(fromDevId, toDevId, cssClass, duration) {
    return new Promise((resolve) => {
      const p1 = coordsFor(fromDevId), p2 = coordsFor(toDevId);
      const dot = el("circle", { class: `packet-dot ${cssClass}`, r: 5.5, cx: p1.x, cy: p1.y });
      fxLayer.appendChild(dot);
      const start = performance.now();
      function step(now) {
        const t = Math.min(1, (now - start) / duration);
        const ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        dot.setAttribute("cx", p1.x + (p2.x - p1.x) * ease);
        dot.setAttribute("cy", p1.y + (p2.y - p1.y) * ease);
        if (t < 1) requestAnimationFrame(step);
        else { fxLayer.removeChild(dot); resolve(); }
      }
      requestAnimationFrame(step);
    });
  }

  function pulseDevice(devId, colorClass) {
    const g = devicesLayer.querySelector(`[data-device-id="${devId}"] .device-body`);
    if (!g) return;
    g.style.filter = "drop-shadow(0 0 8px var(--signal-amber))";
    setTimeout(() => { g.style.filter = ""; }, 380);
  }

  function clearFx() { fxLayer.innerHTML = ""; }

  return {
    setTopology, getTopology, render, setMode, selectDevice,
    animateFrame, pulseDevice, clearFx, deviceCenter,
    get draggingId() { return dragState ? dragState.devId : null; },
    get selectedDeviceId() { return selectedDeviceId; },
    get selectedLinkId() { return selectedLinkId; },
  };
})();
