import ipaddress

from .packet import Packet

BROADCAST_MAC = "ffff.ffff.ffff"
IFACE = "FastEthernet0"


def _dhcp_packet(msg, src_mac, dst_mac, src_ip, dst_ip, sport, dport, data):
    p = Packet(app_data=data)
    p.transport = {"protocol": "UDP", "src_port": sport, "dst_port": dport}
    p.network = {"protocol": "DHCP", "type": msg, "src_ip": src_ip,
                 "dst_ip": dst_ip, "ttl": 64}
    p.datalink = {"src_mac": src_mac, "dst_mac": dst_mac, "ethertype": Packet.ETHERTYPE_IPV4}
    return p


def _frame_steps(topology, hops, packet, subtype, text, src_mac, broadcast, steps):
    """Append one switch_learn + frame step per hop."""
    for hop in hops:
        from_dev = topology.nodes.get(hop["from_device"])
        to_dev = topology.nodes.get(hop["to_device"])
        if to_dev and to_dev.kind == "switch":
            to_dev.learn(src_mac, hop["to_iface"])
            how = ("then floods the broadcast out all other ports" if broadcast
                   else "and forwards directly (unicast, destination MAC known)")
            steps.append({
                "kind": "switch_learn", "device_id": to_dev.id, "device_name": to_dev.name,
                "text": f"{to_dev.name} learns MAC {src_mac} on port {hop['to_iface']}, {how}",
            })
        steps.append({
            "kind": "frame", "subtype": subtype, "link_id": hop["link_id"],
            "from_device": hop["from_device"], "from_name": from_dev.name if from_dev else "?",
            "to_device": hop["to_device"], "to_name": to_dev.name if to_dev else "?",
            "headers": packet.snapshot(), "text": text,
        })


def _info(dev, text, kind="info"):
    return {"kind": kind, "device_id": dev.id, "device_name": dev.name, "text": text}


def _broadcast(topology, pc, packet, subtype, text, steps):
    """Flood `packet` out of the PC's NIC. Returns the list of endpoints reached."""
    endpoints, hops = topology.flood_bfs(pc.id, IFACE)
    _frame_steps(topology, hops, packet, subtype, text,
                 pc.interfaces[IFACE].mac, True, steps)
    return endpoints


def _find_server(topology, endpoints):
    for dev_id, iface_name in endpoints:
        dev = topology.nodes[dev_id]
        if dev.kind == "server" and dev.dhcp_enabled and dev.interfaces[iface_name].enabled:
            return dev, iface_name
    return None, None


def pick_address(topology, server, client_mac):
    """Return a free address for client_mac, or None if the pool is exhausted."""
    if client_mac in server.leases:           # same client asks again -> same address
        return server.leases[client_mac]
    start = ipaddress.ip_address(server.pool_start)
    end = ipaddress.ip_address(server.pool_end)
    taken = set(server.leases.values())
    taken.add(server.interfaces[IFACE].ip)
    cur = start
    while cur <= end:
        ip = str(cur)
        owner, _ = topology.find_iface_owning_ip(ip)
        if ip not in taken and owner is None:  # not leased and not statically in use
            return ip
        cur += 1
    return None


def simulate_dhcp(topology, pc_id):
    steps = []
    pc = topology.nodes.get(pc_id)
    if not pc or pc.kind != "pc":
        return _result(False, steps, "only a PC can request an address via DHCP")
    nic = pc.interfaces[IFACE]
    if not nic.link_id:
        steps.append(_info(pc, f"{pc.name}: no cable plugged into {IFACE}", "unreachable"))
        return _result(False, steps, "no cable connected")
    nic.enabled = True   # a real NIC is up as soon as it has link

    #  1. DISCOVER (broadcast) 
    steps.append(_info(pc, f"{pc.name} has no valid IP. Broadcasting DHCPDISCOVER (0.0.0.0 → 255.255.255.255, UDP 68→67)", "originate"))
    discover = _dhcp_packet("DHCPDISCOVER", nic.mac, BROADCAST_MAC, "0.0.0.0",
                            "255.255.255.255", 68, 67, "Is there a DHCP server? client MAC " + nic.mac)
    endpoints = _broadcast(topology, pc, discover, "dhcp_discover",
                           "DHCPDISCOVER (broadcast) → looking for a DHCP server", steps)

    for dev_id, _i in endpoints:
        dev = topology.nodes[dev_id]
        if dev.kind == "router":
            steps.append(_info(dev, f"{dev.name} receives the broadcast but routers do not forward broadcasts, so it stops here"))

    server, s_iface_name = _find_server(topology, endpoints)
    if not server:
        steps.append(_info(pc, "No DHCP server answered the DHCPDISCOVER. (The server must be on the same LAN, with DHCP enabled and an IP address.)", "unreachable"))
        return _result(False, steps, "no DHCP server found on this LAN")

    s_iface = server.interfaces[s_iface_name]
    if not s_iface.ip:
        steps.append(_info(server, f"{server.name} has DHCP enabled but no IP address of its own, so it cannot reply", "unreachable"))
        return _result(False, steps, "DHCP server has no IP address")

    #  2. OFFER (unicast back to the client MAC) 
    offered = pick_address(topology, server, nic.mac)
    if not offered:
        steps.append(_info(server, f"{server.name}: DHCP pool {server.pool_start} - {server.pool_end} is exhausted, no OFFER sent", "unreachable"))
        return _result(False, steps, "DHCP pool exhausted")
    steps.append(_info(server, f"{server.name} receives DHCPDISCOVER and offers the free address {offered}"))
    offer = _dhcp_packet("DHCPOFFER", s_iface.mac, nic.mac, s_iface.ip, offered, 67, 68,
                         f"Offer {offered} / {server.pool_mask}, gateway {server.pool_gateway}")
    back = topology.shortest_l2_path(server.id, s_iface_name, pc.id, IFACE)
    _frame_steps(topology, back, offer, "dhcp_offer",
                 f"DHCPOFFER (unicast) → you can have {offered}", s_iface.mac, False, steps)

    # 3. REQUEST (broadcast, so any other servers know the offer was taken)
    steps.append(_info(pc, f"{pc.name} accepts the offer and broadcasts DHCPREQUEST for {offered}"))
    request = _dhcp_packet("DHCPREQUEST", nic.mac, BROADCAST_MAC, "0.0.0.0", "255.255.255.255",
                           68, 67, f"I would like {offered} from server {s_iface.ip}")
    _broadcast(topology, pc, request, "dhcp_request",
               f"DHCPREQUEST (broadcast) → I accept {offered}", steps)

    #  4. ACK (unicast)
    server.leases[nic.mac] = offered
    steps.append(_info(server, f"{server.name} records the lease {offered} → {nic.mac} and sends DHCPACK"))
    ack = _dhcp_packet("DHCPACK", s_iface.mac, nic.mac, s_iface.ip, offered, 67, 68,
                       f"Lease confirmed: {offered} / {server.pool_mask}, gateway {server.pool_gateway}")
    _frame_steps(topology, back, ack, "dhcp_ack",
                 f"DHCPACK (unicast) → {offered} is yours", s_iface.mac, False, steps)

    nic.ip, nic.mask = offered, server.pool_mask
    pc.default_gateway = server.pool_gateway
    pc.dhcp_server = s_iface.ip
    pc.arp_table.clear()   # any old ARP entries belong to the previous address
    steps.append(_info(pc, f"{pc.name} configures itself: IP {offered}, mask {server.pool_mask}, gateway {server.pool_gateway}", "success"))

    result = _result(True, steps, None)
    result["lease"] = {"ip": offered, "mask": server.pool_mask,
                       "gateway": server.pool_gateway, "server": s_iface.ip}
    return result


def _result(success, steps, error):
    for i, s in enumerate(steps):
        s["seq"] = i
    return {"success": success, "steps": steps, "error": error}
