var NS = window.NS || (window.NS = {});

NS.simulate = (function () {
  const srcSelect = document.getElementById("pingSrc");
  const dstInput = document.getElementById("pingDst");
  const sendBtn = document.getElementById("pingSendBtn");
  const banner = document.getElementById("pingResultBanner");
  const logEl = document.getElementById("eventLog");
  const ripLogEl = document.getElementById("ripLog");
  const ripBtn = document.getElementById("ripConvergeBtn");

  let playing = false;

  function populateSourceOptions(topology) {
    const prev = srcSelect.value;
    srcSelect.innerHTML = "";
    Object.values(topology.nodes).forEach((dev) => {
      const opt = document.createElement("option");
      opt.value = dev.id;
      opt.textContent = `${dev.name}  (${dev.type})`;
      srcSelect.appendChild(opt);
    });
    if (prev && topology.nodes[prev]) srcSelect.value = prev;
  }

  function setSource(devId) {
    if (srcSelect.querySelector(`option[value="${devId}"]`)) srcSelect.value = devId;
  }

  function clearLog() { logEl.innerHTML = ""; }

  function appendLog(step) {
    const line = document.createElement("div");
    let cls = `log-line k-${step.kind}`;
    if (step.subtype) cls += ` s-${step.subtype}`;
    line.className = cls;
    line.innerHTML = `<span class="log-seq">${String(step.seq).padStart(2, "0")}</span>${escapeHtml(step.text)}`;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function frameClass(step) {
    if (step.subtype === "arp_request" || step.subtype === "dhcp_discover" || step.subtype === "dhcp_request") return "arp";
    if (step.subtype === "arp_reply" || step.subtype === "dhcp_offer" || step.subtype === "dhcp_ack") return "reply";
    return "data";
  }

  async function playSteps(steps) {
    playing = true;
    NS.canvas.clearFx();
    for (const step of steps) {
      appendLog(step);
      if (step.kind === "frame" && step.link_id) {
        await NS.canvas.animateFrame(step.from_device, step.to_device, frameClass(step), 420);
      } else if (step.device_id) {
        NS.canvas.pulseDevice(step.device_id);
        await sleep(140);
      } else {
        await sleep(80);
      }
    }
    playing = false;
  }

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  async function send() {
    if (playing) return;
    const srcId = srcSelect.value;
    const dst = dstInput.value.trim();
    if (!srcId) { NS.app.setStatus("Add a device first.", true); return; }
    if (!dst) { NS.app.setStatus("Enter a destination IP.", true); return; }
    banner.classList.add("hidden");
    clearLog();
    sendBtn.disabled = true;
    try {
      const result = await NS.api.simulatePing(srcId, dst);
      await playSteps(result.steps);
      banner.classList.remove("hidden");
      if (result.success) {
        banner.className = "result-banner success";
        banner.textContent = `✓ Reply received from ${dst}`;
      } else {
        banner.className = "result-banner fail";
        banner.textContent = `✕ Request failed — ${result.error || "unreachable"}`;
      }
      NS.app.refreshQuiet();
    } catch (e) {
      NS.app.setStatus(e.message, true);
    } finally {
      sendBtn.disabled = false;
    }
  }

  async function runDhcp(pcId) {
    if (playing) return;
    document.querySelector('.tab-btn[data-tab="ping"]').click();   // show the Simulate tab
    setSource(pcId);
    banner.classList.add("hidden");
    clearLog();
    try {
      const result = await NS.api.simulateDhcp(pcId);
      await playSteps(result.steps);
      banner.classList.remove("hidden");
      if (result.success) {
        banner.className = "result-banner success";
        banner.textContent = `\u2713 Leased ${result.lease.ip} from ${result.lease.server}`;
      } else {
        banner.className = "result-banner fail";
        banner.textContent = `\u2715 DHCP failed \u2014 ${result.error}`;
      }
      NS.app.refreshQuiet();
    } catch (e) {
      NS.app.setStatus(e.message, true);
    }
  }

  // another user ran a ping / DHCP request: play the same animation here
  async function playRemote(d) {
    while (playing) await sleep(100);          // queue behind any animation already running
    NS.app.setStatus(`${d.by} is running a ${d.label}...`);
    banner.classList.add("hidden");
    clearLog();
    await playSteps(d.steps);
    banner.classList.remove("hidden");
    if (d.success) {
      banner.className = "result-banner success";
      banner.textContent = d.lease ? `\u2713 ${d.by}: leased ${d.lease.ip} from ${d.lease.server}` : `\u2713 ${d.by}'s ${d.label} succeeded`;
    } else {
      banner.className = "result-banner fail";
      banner.textContent = `\u2715 ${d.by}'s ${d.label} failed \u2014 ${d.error || "unreachable"}`;
    }
  }

  sendBtn.addEventListener("click", send);
  dstInput.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

  async function convergeRip() {
    ripBtn.disabled = true;
    ripLogEl.innerHTML = "";
    try {
      const result = await NS.api.ripConverge();
      result.log.forEach((line, i) => {
        const div = document.createElement("div");
        div.className = "log-line";
        div.innerHTML = `<span class="log-seq">${String(i).padStart(2, "0")}</span>${escapeHtml(line)}`;
        ripLogEl.appendChild(div);
      });
      NS.canvas.setTopology(result.topology);
      NS.app.refreshQuiet();
    } catch (e) {
      NS.app.setStatus(e.message, true);
    } finally {
      ripBtn.disabled = false;
    }
  }
  ripBtn.addEventListener("click", convergeRip);

  return { populateSourceOptions, setSource, runDhcp, playRemote };
})();
