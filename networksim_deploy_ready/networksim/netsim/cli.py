import ipaddress

from . import dhcp, engine
from .routing import converge_rip

PC_HELP_TEXT = (
    "Available PC commands:\n"
    "  ip address <ip> <mask> [gateway]  - configure this PC's address\n"
    "  ip default-gateway <ip>           - set the default gateway\n"
    "  show ip  |  ipconfig              - show IP configuration\n"
    "  ping <ip>                         - send an ICMP echo\n"
    "  arp -a                            - show the ARP cache\n"
    "  ip dhcp                           - request an address from a DHCP server\n"
    "  hostname <name>                   - rename this PC"
)

SERVER_HELP_TEXT = (
    "Available server commands:\n"
    "  ip address <ip> <mask> [gateway]  - give the server a static address\n"
    "  dhcp pool <first> <last> <mask> <gateway>  - enable DHCP with this pool\n"
    "  no dhcp                           - disable the DHCP service\n"
    "  show dhcp                         - show the pool and current leases\n"
    "  show ip  |  ipconfig              - show IP configuration\n"
    "  ping <ip>                         - send an ICMP echo\n"
    "  arp -a                            - show the ARP cache\n"
    "  hostname <name>                   - rename this server"
)


def tok_match(token, *fulls):
    if not token:
        return False
    t = token.lower()
    return any(full.lower().startswith(t) for full in fulls)


def _valid_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _valid_mask(s):
    return _valid_ip(s)


class CLI:
    def __init__(self, topology):
        self.topology = topology

    def prompt(self, dev):
        if dev.kind in ("pc", "server"):
            return f"{dev.name}>"
        suffix = {
            "user": ">", "priv": "#", "config": "(config)#",
            "config-if": "(config-if)#", "config-router": "(config-router)#",
        }.get(dev.cli_mode, ">")
        return f"{dev.name}{suffix}"

    def execute(self, device_id, line):
        dev = self.topology.nodes.get(device_id)
        if not dev:
            return {"output": "% Device not found", "prompt": ""}
        line = line.strip()
        if not line:
            return {"output": "", "prompt": self.prompt(dev), "mode": dev.cli_mode}
        try:
            if dev.kind == "server":
                output = self._exec_server(dev, line)
            elif dev.kind == "pc":
                output = self._exec_pc(dev, line)
            else:
                output = self._exec_ios(dev, line)
        except Exception as e:  # keep the terminal alive even on a bug
            output = f"% Error: {e}"
        return {"output": output, "prompt": self.prompt(dev), "mode": dev.cli_mode}

    # The PC shell 
    def _exec_pc(self, dev, line):
        tokens = line.split()
        cmd = tokens[0].lower()
        iface = dev.interfaces["FastEthernet0"]

        if cmd in ("help", "?"):
            return PC_HELP_TEXT
        if (cmd == "ip" and len(tokens) > 1 and tokens[1].lower() == "dhcp") or cmd == "dhcp":
            if dev.kind != "pc":
                return "% A server must be given a static address: ip address <ip> <mask>"
            result = dhcp.simulate_dhcp(self.topology, dev.id)
            if not result["success"]:
                return f"DHCP failed: {result['error']}"
            lease = result["lease"]
            return (f"DHCPDISCOVER -> DHCPOFFER -> DHCPREQUEST -> DHCPACK\n"
                    f"Leased {lease['ip']} / {lease['mask']} from {lease['server']}\n"
                    f"Default gateway: {lease['gateway']}")
        if cmd == "ip" and len(tokens) > 1 and tok_match(tokens[1], "address"):
            if len(tokens) < 4:
                return "% Usage: ip address <ip> <mask> [gateway]"
            ip, mask = tokens[2], tokens[3]
            if not (_valid_ip(ip) and _valid_mask(mask)):
                return "% Invalid IP address or subnet mask"
            iface.ip, iface.mask, iface.enabled = ip, mask, True
            dev.dhcp_server = None
            out = f"FastEthernet0 configured with {ip} {mask}"
            if len(tokens) >= 5:
                dev.default_gateway = tokens[4]
                out += f", default gateway {tokens[4]}"
            return out
        if cmd == "ip" and len(tokens) > 1 and tok_match(tokens[1], "default-gateway"):
            if len(tokens) < 3:
                return "% Usage: ip default-gateway <ip>"
            dev.default_gateway = tokens[2]
            return f"Default gateway set to {tokens[2]}"
        if cmd == "ipconfig" or (cmd == "show" and len(tokens) > 1 and tok_match(tokens[1], "ip")):
            return (f"FastEthernet0 ... IP: {iface.ip or 'unassigned'}  "
                    f"Mask: {iface.mask or '-'}  MAC: {iface.mac}\n"
                    f"Default Gateway: {dev.default_gateway or 'unassigned'}\n"
                    f"Addressing: {'DHCP (server ' + dev.dhcp_server + ')' if dev.dhcp_server else 'static'}")
        if cmd == "ping":
            if len(tokens) < 2:
                return "% Usage: ping <ip address>"
            text, _ = engine.cli_ping_text(self.topology, dev.id, tokens[1])
            return text
        if cmd == "arp" and len(tokens) > 1 and tokens[1] == "-a":
            if not dev.arp_table:
                return "No ARP entries"
            return "\n".join(f"Internet address: {ip}   Physical address: {e['mac']}"
                              for ip, e in dev.arp_table.items())
        if cmd == "hostname":
            if len(tokens) < 2:
                return "% Usage: hostname <name>"
            dev.name = tokens[1]
            return f"Hostname set to {tokens[1]}"
        return f"'{tokens[0]}' is not recognized. Type 'help' for a list of commands."

    # The server shell (PC commands + DHCP service) 
    def _exec_server(self, dev, line):
        tokens = line.split()
        low = [t.lower() for t in tokens]
        if low[0] == "dhcp" and len(low) > 1 and low[1] == "pool":
            if len(tokens) < 6:
                return "% Usage: dhcp pool <first-ip> <last-ip> <mask> <gateway>"
            start, end, mask, gw = tokens[2:6]
            if not all(_valid_ip(x) for x in (start, end, mask, gw)):
                return "% Invalid IP address in pool definition"
            if ipaddress.ip_address(start) > ipaddress.ip_address(end):
                return "% First address must not be higher than the last address"
            dev.pool_start, dev.pool_end, dev.pool_mask, dev.pool_gateway = start, end, mask, gw
            dev.dhcp_enabled = True
            dev.leases.clear()
            return f"DHCP service enabled: pool {start} - {end}, mask {mask}, gateway {gw}"
        if low[0] == "no" and len(low) > 1 and low[1] == "dhcp":
            dev.dhcp_enabled = False
            return "DHCP service disabled"
        if low[0] == "show" and len(low) > 1 and low[1] == "dhcp":
            if not dev.dhcp_enabled:
                return "DHCP service is not enabled. Use: dhcp pool <first-ip> <last-ip> <mask> <gateway>"
            lines = [f"DHCP pool: {dev.pool_start} - {dev.pool_end}",
                     f"Mask: {dev.pool_mask}   Gateway: {dev.pool_gateway}",
                     f"Leases ({len(dev.leases)}):"]
            lines += [f"  {ip:<16}{mac}" for mac, ip in dev.leases.items()] or ["  (none)"]
            return "\n".join(lines)
        if low[0] in ("help", "?"):
            return SERVER_HELP_TEXT
        return self._exec_pc(dev, line)

    #  IOS-like shell (router / switch) 
    def _exec_ios(self, dev, line):
        tokens = line.split()
        low = [t.lower() for t in tokens]

        if tok_match(low[0], "help") or tokens[0] == "?":
            return self._help_text(dev)
        if tok_match(low[0], "exit"):
            return self._do_exit(dev)
        if tok_match(low[0], "end") and dev.cli_mode in ("config", "config-if", "config-router"):
            dev.cli_mode = "priv"
            dev.current_iface = None
            return ""
        if tok_match(low[0], "enable") and dev.cli_mode == "user":
            dev.cli_mode = "priv"
            return ""
        if tok_match(low[0], "disable") and dev.cli_mode != "user":
            dev.cli_mode = "user"
            return ""
        if low[0] in ("show", "sh"):
            return self._show(dev, low[1:], tokens[1:])
        if low[0] == "ping":
            if len(tokens) < 2:
                return "% Usage: ping <ip-address>"
            text, _ = engine.cli_ping_text(self.topology, dev.id, tokens[1])
            return text

        if dev.cli_mode == "user":
            return "% Unknown command or computer name, or unauthorized -- try 'enable' first"
        if dev.cli_mode == "priv":
            return self._priv(dev, low, tokens)
        if dev.cli_mode == "config":
            return self._config(dev, low, tokens)
        if dev.cli_mode == "config-if":
            return self._config_if(dev, low, tokens)
        if dev.cli_mode == "config-router":
            return self._config_router(dev, low, tokens)
        return "% Unknown mode"

    def _do_exit(self, dev):
        if dev.cli_mode in ("config-if", "config-router"):
            dev.cli_mode = "config"
            dev.current_iface = None
        elif dev.cli_mode == "config":
            dev.cli_mode = "priv"
        elif dev.cli_mode == "priv":
            dev.cli_mode = "user"
        return ""

    def _priv(self, dev, low, tokens):
        if tok_match(low[0], "configure") and len(low) > 1 and tok_match(low[1], "terminal"):
            dev.cli_mode = "config"
            return "Enter configuration commands, one per line.  End with CNTL/Z."
        if tok_match(low[0], "write") or tok_match(low[0], "copy"):
            return "Building configuration...\n[OK]"
        if tok_match(low[0], "reload"):
            return "Proceed with reload? [confirm]  (simulated - no reload occurs)"
        if dev.kind == "router" and low[0] == "converge-rip":
            log, rounds, _ = converge_rip(self.topology)
            return "\n".join(log)
        return "% Invalid input detected"

    def _config(self, dev, low, tokens):
        if tok_match(low[0], "hostname"):
            if len(tokens) < 2:
                return "% Usage: hostname <name>"
            dev.name = tokens[1]
            return ""
        if tok_match(low[0], "interface"):
            if len(tokens) < 2:
                return "% Usage: interface <name>"
            name = dev.resolve_iface_name(tokens[1])
            if not name:
                return f"% Invalid interface reference: {tokens[1]}"
            dev.current_iface = name
            dev.cli_mode = "config-if"
            return ""
        if dev.kind == "router" and tok_match(low[0], "ip") and len(low) > 1 and tok_match(low[1], "route"):
            if len(tokens) < 5:
                return "% Usage: ip route <network> <mask> <next-hop>"
            net, mask, nh = tokens[2], tokens[3], tokens[4]
            if not (_valid_ip(net) and _valid_mask(mask) and _valid_ip(nh)):
                return "% Invalid address"
            dev.add_static_route(net, mask, nh)
            return ""
        if dev.kind == "router" and tok_match(low[0], "no") and len(low) > 1 and tok_match(low[1], "ip") \
                and len(low) > 2 and tok_match(low[2], "route"):
            if len(tokens) < 5:
                return "% Usage: no ip route <network> <mask> <next-hop>"
            dev.remove_static_route(tokens[3], tokens[4])
            return ""
        if dev.kind == "router" and tok_match(low[0], "router") and len(low) > 1 and tok_match(low[1], "rip"):
            dev.rip_enabled = True
            dev.cli_mode = "config-router"
            return ""
        if dev.kind == "router" and tok_match(low[0], "no") and len(low) > 1 and tok_match(low[1], "router") \
                and len(low) > 2 and tok_match(low[2], "rip"):
            dev.rip_enabled = False
            dev.rip_networks.clear()
            return ""
        return "% Invalid input detected"

    def _config_if(self, dev, low, tokens):
        iface = dev.interfaces[dev.current_iface]
        if tok_match(low[0], "ip") and len(low) > 1 and tok_match(low[1], "address"):
            if len(tokens) < 4:
                return "% Usage: ip address <ip> <mask>"
            ip, mask = tokens[2], tokens[3]
            if not (_valid_ip(ip) and _valid_mask(mask)):
                return "% Invalid IP address or mask"
            iface.ip, iface.mask = ip, mask
            if dev.kind == "router":
                dev.rebuild_connected_routes()
            return ""
        if tok_match(low[0], "no") and len(low) > 1 and tok_match(low[1], "ip") and len(low) > 2 \
                and tok_match(low[2], "address"):
            iface.ip, iface.mask = None, None
            if dev.kind == "router":
                dev.rebuild_connected_routes()
            return ""
        if tok_match(low[0], "shutdown"):
            iface.enabled = False
            if dev.kind == "router":
                dev.rebuild_connected_routes()
            return f"%LINK-5-CHANGED: Interface {iface.name}, changed state to administratively down"
        if tok_match(low[0], "no") and len(low) > 1 and tok_match(low[1], "shutdown"):
            iface.enabled = True
            if dev.kind == "router":
                dev.rebuild_connected_routes()
            state = "up" if iface.link_id else "down (no cable connected)"
            return f"%LINK-5-CHANGED: Interface {iface.name}, changed state to {state}"
        return "% Invalid input detected"

    def _config_router(self, dev, low, tokens):
        if tok_match(low[0], "network"):
            if len(tokens) < 2:
                return "% Usage: network <network-address>"
            dev.rip_networks.add(tokens[1])
            return ""
        if tok_match(low[0], "no") and len(low) > 1 and tok_match(low[1], "network"):
            if len(tokens) >= 3:
                dev.rip_networks.discard(tokens[2])
            return ""
        if tok_match(low[0], "version"):
            return ""
        return "% Invalid input detected"

    #  show commands 
    def _show(self, dev, low_rest, raw_rest):
        if not low_rest:
            return "% Incomplete command"
        if tok_match(low_rest[0], "running-config"):
            return self._running_config(dev)
        if tok_match(low_rest[0], "version"):
            return self._show_version(dev)
        if tok_match(low_rest[0], "arp"):
            return self._show_arp(dev)

        if dev.kind == "router":
            if tok_match(low_rest[0], "ip") and len(low_rest) > 1:
                if tok_match(low_rest[1], "route"):
                    return self._show_ip_route(dev)
                if tok_match(low_rest[1], "interface") and len(low_rest) > 2 and tok_match(low_rest[2], "brief"):
                    return self._show_ip_int_brief(dev)
                if tok_match(low_rest[1], "arp"):
                    return self._show_arp(dev)
                if tok_match(low_rest[1], "protocols"):
                    return self._show_ip_protocols(dev)
            if tok_match(low_rest[0], "interfaces"):
                return self._show_ip_int_brief(dev)

        if dev.kind == "switch":
            if tok_match(low_rest[0], "mac-address-table"):
                return self._show_mac_table(dev)
            if tok_match(low_rest[0], "interfaces") or (
                    tok_match(low_rest[0], "ip") and len(low_rest) > 1 and tok_match(low_rest[1], "interface")):
                return self._show_ip_int_brief(dev)

        return f"% Invalid input detected ('show {' '.join(raw_rest)}')"

    def _show_ip_route(self, dev):
        lines = ["Codes: C - connected, S - static, R - RIP", ""]
        gw = next((r for r in dev.routing_table if str(r.network) == "0.0.0.0/0"), None)
        if gw:
            lines.append(f"Gateway of last resort is {gw.next_hop} to network 0.0.0.0")
            lines.append("")
        if not dev.routing_table:
            lines.append("(no routes in table)")
        for r in sorted(dev.routing_table, key=lambda r: (-r.network.prefixlen, str(r.network))):
            if r.source == "C":
                lines.append(f"C    {r.network} is directly connected, {r.iface}")
            elif r.source == "S":
                lines.append(f"S    {r.network} [1/0] via {r.next_hop}")
            elif r.source == "R":
                lines.append(f"R    {r.network} [120/{r.metric}] via {r.next_hop}, {r.iface}")
        return "\n".join(lines)

    def _show_ip_int_brief(self, dev):
        lines = [f"{'Interface':<24}{'IP-Address':<16}{'Status':<24}{'Protocol'}"]
        for name, iface in dev.interfaces.items():
            ip = iface.ip or "unassigned"
            admin = "up" if iface.enabled else "administratively down"
            proto = "up" if (iface.enabled and iface.link_id) else "down"
            lines.append(f"{name:<24}{ip:<16}{admin:<24}{proto}")
        return "\n".join(lines)

    def _show_arp(self, dev):
        if not getattr(dev, "arp_table", None):
            return "(no ARP entries)"
        lines = [f"{'Protocol':<10}{'Address':<16}{'Hardware Addr':<16}{'Type':<9}{'Interface'}"]
        for ip, e in dev.arp_table.items():
            lines.append(f"{'Internet':<10}{ip:<16}{e['mac']:<16}{e.get('type','dynamic'):<9}{e.get('iface','-')}")
        return "\n".join(lines)

    def _show_mac_table(self, dev):
        if not dev.mac_table:
            return "(no entries)"
        lines = ["Vlan    Mac Address       Type      Ports", "----    -----------       ----      -----"]
        for mac, info in dev.mac_table.items():
            lines.append(f"1       {mac}    DYNAMIC   {info['iface']}")
        return "\n".join(lines)

    def _show_ip_protocols(self, dev):
        if not dev.rip_enabled:
            return "Routing Protocol is not enabled"
        lines = ['Routing Protocol is "rip"', "  Sending updates every 30 seconds", "  Routing for Networks:"]
        for n in sorted(dev.rip_networks):
            lines.append(f"    {n}")
        return "\n".join(lines)

    def _show_version(self, dev):
        model = {"router": "NetSim ISR-4000 (simulated)", "switch": "NetSim Catalyst-2960 (simulated)"}.get(dev.kind, "Generic")
        return (f"NetSim IOS Software\n{dev.name} uptime is simulated\n"
                f"Hardware: {model}\n{len(dev.interfaces)} network interfaces")

    def _running_config(self, dev):
        lines = ["Building configuration...", "", "!", f"hostname {dev.name}", "!"]
        for name, iface in dev.interfaces.items():
            lines.append(f"interface {name}")
            if iface.ip:
                lines.append(f" ip address {iface.ip} {iface.mask}")
            lines.append(" no shutdown" if iface.enabled else " shutdown")
            lines.append("!")
        if dev.kind == "router":
            for r in dev.routing_table:
                if r.source == "S":
                    lines.append(f"ip route {r.network.network_address} {r.network.netmask} {r.next_hop}")
            if dev.rip_enabled:
                lines.append("router rip")
                for n in sorted(dev.rip_networks):
                    lines.append(f" network {n}")
                lines.append("!")
        lines.append("end")
        return "\n".join(lines)

    def _help_text(self, dev):
        if dev.kind in ("pc", "server"):
            return PC_HELP_TEXT
        common = [
            "Commands (unambiguous abbreviations OK, e.g. 'conf t', 'int g0/0', 'sh ip rou'):",
            "  enable / disable              - privileged EXEC mode",
            "  configure terminal            - global configuration mode",
            "  interface <name>               - configure an interface",
            "  ip address <ip> <mask>         - assign an interface IP (config-if)",
            "  shutdown / no shutdown         - disable / enable an interface",
            "  ping <ip>                      - send an ICMP echo",
            "  show ip interface brief        - interface status summary",
            "  show running-config            - view device configuration",
        ]
        if dev.kind == "router":
            common += [
                "  ip route <net> <mask> <nh>    - add a static route",
                "  router rip / network <net>    - enable RIP on a network (config-router)",
                "  show ip route                  - view the routing table",
                "  show arp                       - view the ARP cache",
            ]
        else:
            common += ["  show mac-address-table        - view the switch's learned MAC/CAM table"]
        return "\n".join(common)
