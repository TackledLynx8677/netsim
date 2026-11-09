var NS = window.NS || (window.NS = {});

NS.cliPanel = (function () {
  const empty = document.getElementById("cliEmpty");
  const term = document.getElementById("cliTerminal");
  const scrollback = document.getElementById("cliScrollback");
  const promptEl = document.getElementById("cliPrompt");
  const input = document.getElementById("cliInput");
  const nameEl = document.getElementById("cliDeviceName");

  let currentDeviceId = null;
  const histories = {};   // deviceId -> [commands]
  const historyPos = {};  // deviceId -> pointer

  function motd(dev) {
    if (dev.type === "pc" || dev.type === "server") {
      return `NetSim Desktop — ${dev.name}\nType 'help' for available commands.\n`;
    }
    return `NetSim IOS Terminal — ${dev.name}\nUser Access Verification bypassed (simulator).\nType 'help' or '?' for available commands.\n`;
  }

  function open(dev) {
    currentDeviceId = dev.id;
    empty.classList.add("hidden");
    term.classList.remove("hidden");
    nameEl.textContent = `${dev.name} — console`;
    if (!scrollback.dataset.initFor || scrollback.dataset.initFor !== dev.id) {
      scrollback.textContent = motd(dev);
      scrollback.dataset.initFor = dev.id;
    }
    if (!histories[dev.id]) histories[dev.id] = [];
    historyPos[dev.id] = histories[dev.id].length;
    NS.api.cliPrompt(dev.id).then((r) => { promptEl.textContent = r.prompt; });
    scrollback.scrollTop = scrollback.scrollHeight;
  }

  function close() {
    currentDeviceId = null;
    empty.classList.remove("hidden");
    term.classList.add("hidden");
  }

  function print(text) {
    scrollback.textContent += text;
    scrollback.scrollTop = scrollback.scrollHeight;
  }

  async function runCommand(cmdText) {
    if (!currentDeviceId) return;
    print(`${promptEl.textContent} ${cmdText}\n`);
    histories[currentDeviceId].push(cmdText);
    historyPos[currentDeviceId] = histories[currentDeviceId].length;
    const res = await NS.api.runCli(currentDeviceId, cmdText);
    if (res.output) print(res.output + "\n");
    promptEl.textContent = res.prompt || promptEl.textContent;
    NS.app.refreshQuiet();
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const val = input.value;
      input.value = "";
      if (val.trim().length === 0) { print(`${promptEl.textContent} \n`); return; }
      runCommand(val);
    } else if (e.key === "ArrowUp") {
      if (!currentDeviceId) return;
      const h = histories[currentDeviceId];
      if (historyPos[currentDeviceId] > 0) {
        historyPos[currentDeviceId]--;
        input.value = h[historyPos[currentDeviceId]];
        setTimeout(() => input.setSelectionRange(input.value.length, input.value.length), 0);
      }
      e.preventDefault();
    } else if (e.key === "ArrowDown") {
      if (!currentDeviceId) return;
      const h = histories[currentDeviceId];
      if (historyPos[currentDeviceId] < h.length - 1) {
        historyPos[currentDeviceId]++;
        input.value = h[historyPos[currentDeviceId]];
      } else {
        historyPos[currentDeviceId] = h.length;
        input.value = "";
      }
      e.preventDefault();
    }
  });

  function focusInput() { input.focus(); }

  return { open, close, focusInput, get currentDeviceId() { return currentDeviceId; } };
})();
