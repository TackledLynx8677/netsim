"""
The packet simulation engine.

This is where a "ping" actually gets modeled the way a real network would
handle it:
  1. The source host decides whether the destination is on its own subnet
     or must be sent via a gateway/route (first_hop_decision).
  2. It resolves the next hop's MAC address via ARP - broadcast flood
     through switches, unicast reply back (resolve_arp).
  3. Switches along the way learn source MACs into their CAM table and
     either forward out a known port or flood.
  4. The frame is delivered to the next router or the final host. Routers
     decapsulate, decrement TTL, and re-run the same routing decision for
     the *next* hop toward the ultimate destination (_deliver's loop).
  5. On arrival, an ICMP Echo Reply is generated and driven back through
     the same machinery in the opposite direction.
"""
import ipaddress

from .packet import Packet

MAX_HOPS = 16


def first_hop_decision(topology, dev, dst_ip):
    """
    Decide how `dev` should get a packet to dst_ip from here.
    Returns (egress_iface_name, next_hop_ip, source_ip_for_packet, error).
    Works for both hosts (PC) and routers, and is re-used at every router
    hop along the path (the destination IP never changes, just who's
    making the decision).
    """
    try:
        dst = ipaddress.ip_address(dst_ip)
    except ValueError:
        return None, None, None, f"'{dst_ip}' is not a valid IP address"

    # Is the destination directly on one of this device's own subnets?
    for name, iface in dev.interfaces.items():
        if iface.enabled and iface.link_id and iface.network and dst in iface.network:
            return name, dst_ip, iface.ip, None

    if dev.kind in ("pc", "server"):
        if not dev.interfaces["FastEthernet0"].ip:
            return None, None, None, "device has no IP address configured"
        if not dev.default_gateway:
            return None, None, None, "destination unreachable: no default gateway configured"
        return "FastEthernet0", dev.default_gateway, dev.interfaces["FastEthernet0"].ip, None

    if dev.kind == "router":
        route = dev.lookup_route(dst_ip)
        if not route:
            return None, None, None, "no route to destination (routing table has no match)"
        if route.source == "C":
            return route.iface, dst_ip, dev.interfaces[route.iface].ip, None
        egress = route.iface or resolve_egress_for_nexthop(dev, route.next_hop)
        if not egress:
            return None, None, None, f"next hop {route.next_hop} is not directly reachable"
        return egress, route.next_hop, dev.interfaces[egress].ip, None

    return None, None, None, f"{dev.kind} cannot originate or forward IP traffic"


def resolve_egress_for_nexthop(router, next_hop_ip):
    ip = ipaddress.ip_address(next_hop_ip)
    for route in router.connected_routes():
        if ip in route.network:
            return route.iface
    return None


def resolve_arp(topology, dev, egress_iface_name, target_ip, steps):
    """Resolve target_ip -> MAC out of dev's egress interface, animating
    the ARP broadcast/reply if it isn't already cached."""
    iface = dev.interfaces[egress_iface_name]

    cached = dev.arp_table.get(target_ip)
    if cached:
        steps.append({
            "kind": "info", "device_id": dev.id, "device_name": dev.name,
            "text": f"{dev.name}: ARP cache hit for {target_ip} \u2192 {cached['mac']}",
        })
        return cached["mac"]

    steps.append({
        "kind": "arp_start", "device_id": dev.id, "device_name": dev.name,
        "text": f"{dev.name}: no ARP entry for {target_ip}. Broadcasting \"who has {target_ip}?\" out {egress_iface_name}.",
    })
    endpoints, hops = topology.flood_bfs(dev.id, egress_iface_name)
    req_packet = Packet.new_arp_request(iface.ip, iface.mac, target_ip)

    for hop in hops:
        from_dev = topology.nodes.get(hop["from_device"])
        to_dev = topology.nodes.get(hop["to_device"])
        if to_dev and to_dev.kind == "switch":
            to_dev.learn(iface.mac, hop["to_iface"])
            steps.append({
                "kind": "switch_learn", "device_id": to_dev.id, "device_name": to_dev.name,
                "text": f"{to_dev.name} learns MAC {iface.mac} on port {hop['to_iface']}, then floods the broadcast out all other ports",
            })
        steps.append({
            "kind": "frame", "subtype": "arp_request", "link_id": hop["link_id"],
            "from_device": hop["from_device"], "from_name": from_dev.name if from_dev else "?",
            "to_device": hop["to_device"], "to_name": to_dev.name if to_dev else "?",
            "headers": req_packet.snapshot(),
            "text": f"ARP request (broadcast) \u2192 who has {target_ip}? tell {iface.ip}",
        })

    target_dev, target_iface_name = None, None
    for edev_id, eiface_name in endpoints:
        edev = topology.nodes[edev_id]
        eiface = edev.interfaces[eiface_name]
        if eiface.ip == target_ip and eiface.enabled:
            target_dev, target_iface_name = edev, eiface_name
            break

    if not target_dev:
        steps.append({
            "kind": "unreachable", "device_id": dev.id, "device_name": dev.name,
            "text": f"No device answered the ARP request for {target_ip}. Request timed out.",
        })
        return None

    target_iface = target_dev.interfaces[target_iface_name]
    steps.append({
        "kind": "info", "device_id": target_dev.id, "device_name": target_dev.name,
        "text": f"{target_dev.name} owns {target_ip} and replies with its MAC {target_iface.mac}",
    })
    reply_path = topology.shortest_l2_path(target_dev.id, target_iface_name, dev.id, egress_iface_name)
    reply_packet = Packet.new_arp_reply(target_iface.ip, target_iface.mac, iface.ip, iface.mac)
    for hop in reply_path:
        from_dev = topology.nodes.get(hop["from_device"])
        to_dev = topology.nodes.get(hop["to_device"])
        if to_dev and to_dev.kind == "switch":
            to_dev.learn(target_iface.mac, hop["to_iface"])
            steps.append({
                "kind": "switch_learn", "device_id": to_dev.id, "device_name": to_dev.name,
                "text": f"{to_dev.name} learns MAC {target_iface.mac} on port {hop['to_iface']} and forwards directly (unicast, destination MAC known)",
            })
        steps.append({
            "kind": "frame", "subtype": "arp_reply", "link_id": hop["link_id"],
            "from_device": hop["from_device"], "from_name": from_dev.name if from_dev else "?",
            "to_device": hop["to_device"], "to_name": to_dev.name if to_dev else "?",
            "headers": reply_packet.snapshot(),
            "text": f"ARP reply (unicast) \u2192 {target_ip} is at {target_iface.mac}",
        })

    dev.arp_table[target_ip] = {"mac": target_iface.mac, "iface": egress_iface_name, "type": "dynamic"}
    target_dev.arp_table[iface.ip] = {"mac": iface.mac, "iface": target_iface_name, "type": "dynamic"}
    return target_iface.mac


def _deliver(topology, origin_dev, final_dst_ip, packet, steps):
    """Drive `packet` from origin_dev to final_dst_ip, hop by hop.
    Returns (success, arrived_dev, arrived_iface, error)."""
    current_dev = origin_dev
    hops_taken = 0

    while True:
        hops_taken += 1
        if hops_taken > MAX_HOPS:
            steps.append({
                "kind": "unreachable", "device_id": current_dev.id, "device_name": current_dev.name,
                "text": f"Hop limit ({MAX_HOPS}) exceeded - possible routing loop.",
            })
            return False, None, None, "hop limit exceeded"

        egress_iface, next_hop_ip, _src_ip, err = first_hop_decision(topology, current_dev, final_dst_ip)
        if err:
            steps.append({
                "kind": "unreachable", "device_id": current_dev.id, "device_name": current_dev.name,
                "text": f"{current_dev.name}: {err}",
            })
            return False, None, None, err

        dst_mac = resolve_arp(topology, current_dev, egress_iface, next_hop_ip, steps)
        if not dst_mac:
            return False, None, None, f"ARP resolution failed for {next_hop_ip}"

        src_iface_obj = current_dev.interfaces[egress_iface]
        packet.set_frame(src_iface_obj.mac, dst_mac)

        target_dev, target_iface_name = topology.find_iface_owning_ip(next_hop_ip)
        if not target_dev:
            steps.append({
                "kind": "unreachable", "device_id": current_dev.id, "device_name": current_dev.name,
                "text": "Internal routing error: next hop vanished after ARP resolved.",
            })
            return False, None, None, "internal error"

        path = topology.shortest_l2_path(current_dev.id, egress_iface, target_dev.id, target_iface_name)
        for hop in path:
            from_dev = topology.nodes.get(hop["from_device"])
            to_dev = topology.nodes.get(hop["to_device"])
            if to_dev and to_dev.kind == "switch":
                known = to_dev.lookup(dst_mac)
                to_dev.learn(src_iface_obj.mac, hop["to_iface"])
                verb = "forwards (unicast, MAC known)" if known else "floods (destination MAC unknown)"
                steps.append({
                    "kind": "switch_learn", "device_id": to_dev.id, "device_name": to_dev.name,
                    "text": f"{to_dev.name} learns {src_iface_obj.mac} on port {hop['to_iface']} and {verb}",
                })
            steps.append({
                "kind": "frame", "subtype": "data", "link_id": hop["link_id"],
                "from_device": hop["from_device"], "from_name": from_dev.name if from_dev else "?",
                "to_device": hop["to_device"], "to_name": to_dev.name if to_dev else "?",
                "headers": packet.snapshot(),
                "text": f"Frame [{src_iface_obj.mac} \u2192 {dst_mac}] carrying IP packet ({packet.network.get('src_ip')} \u2192 {packet.network.get('dst_ip')})",
            })

        if next_hop_ip == final_dst_ip:
            steps.append({
                "kind": "arrived", "device_id": target_dev.id, "device_name": target_dev.name,
                "text": f"{target_dev.name} receives the frame on {target_iface_name}; destination IP {final_dst_ip} matches this interface \u2014 decapsulated up the stack.",
            })
            return True, target_dev, target_iface_name, None

        if target_dev.kind != "router":
            steps.append({
                "kind": "unreachable", "device_id": target_dev.id, "device_name": target_dev.name,
                "text": f"{target_dev.name} is not a router and cannot forward toward {final_dst_ip}.",
            })
            return False, None, None, "next hop cannot forward"

        packet.network["ttl"] -= 1
        steps.append({
            "kind": "route", "device_id": target_dev.id, "device_name": target_dev.name,
            "text": f"{target_dev.name} decrements TTL to {packet.network['ttl']} and looks up {final_dst_ip} in its routing table",
        })
        if packet.network["ttl"] <= 0:
            steps.append({
                "kind": "ttl_expired", "device_id": target_dev.id, "device_name": target_dev.name,
                "text": f"TTL expired at {target_dev.name} (ICMP Time Exceeded).",
            })
            return False, None, None, "TTL expired in transit"

        current_dev = target_dev


def _finalize(steps):
    for i, s in enumerate(steps):
        s["seq"] = i
    return steps


def simulate_echo(topology, src_dev_id, dst_ip):
    """Full ping simulation: request out, reply back. Returns a dict with
    a full, animation-ready step log."""
    steps = []
    src_dev = topology.nodes.get(src_dev_id)
    if not src_dev:
        return {"success": False, "steps": [], "error": "source device not found"}

    egress_iface, next_hop_ip, src_ip, err = first_hop_decision(topology, src_dev, dst_ip)
    if err:
        steps.append({
            "kind": "unreachable", "device_id": src_dev.id, "device_name": src_dev.name,
            "text": f"{src_dev.name}: {err}",
        })
        return {"success": False, "steps": _finalize(steps), "error": err}

    echo = Packet.new_icmp_echo(src_ip, dst_ip, ttl=64)
    steps.append({
        "kind": "originate", "device_id": src_dev.id, "device_name": src_dev.name,
        "text": f"{src_dev.name} originates ICMP Echo Request: {src_ip} \u2192 {dst_ip} (TTL=64)",
        "headers": echo.snapshot(),
    })

    ok, arrived_dev, arrived_iface, err = _deliver(topology, src_dev, dst_ip, echo, steps)
    if not ok:
        return {"success": False, "steps": _finalize(steps), "error": err}

    reply_src_ip = arrived_dev.interfaces[arrived_iface].ip
    reply = Packet.new_icmp_reply(echo, reply_src_ip)
    steps.append({
        "kind": "reply_start", "device_id": arrived_dev.id, "device_name": arrived_dev.name,
        "text": f"{arrived_dev.name} sends ICMP Echo Reply: {reply_src_ip} \u2192 {src_ip}",
        "headers": reply.snapshot(),
    })

    ok2, _back_dev, _back_iface, err2 = _deliver(topology, arrived_dev, src_ip, reply, steps)
    if not ok2:
        return {"success": False, "steps": _finalize(steps), "error": err2}

    steps.append({
        "kind": "success", "device_id": src_dev.id, "device_name": src_dev.name,
        "text": f"{src_dev.name} receives the Echo Reply from {dst_ip}. Ping succeeded.",
    })
    return {"success": True, "steps": _finalize(steps), "error": None}


def cli_ping_text(topology, src_dev_id, dst_ip, count=5):
    """Runs the simulation `count` times and formats classic IOS-style
    ping output (also returns the last run's full step log for reference)."""
    marks = []
    last_result = None
    for _ in range(count):
        result = simulate_echo(topology, src_dev_id, dst_ip)
        last_result = result
        marks.append("!" if result["success"] else ".")
    success_count = marks.count("!")
    pct = int(100 * success_count / count) if count else 0
    lines = [
        f"Type escape sequence to abort.",
        f"Sending {count}, 100-byte ICMP Echos to {dst_ip}, timeout is 2 seconds:",
        "".join(marks),
        f"Success rate is {pct} percent ({success_count}/{count})" +
        ("" if success_count else f"  [{last_result['error']}]" if last_result and last_result.get("error") else ""),
    ]
    return "\n".join(lines), last_result
