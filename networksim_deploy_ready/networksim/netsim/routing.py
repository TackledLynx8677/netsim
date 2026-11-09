import ipaddress

from .models import Route


def _iface_in_rip(router, iface):
    """Does this interface's connected network fall under a `network` statement?"""
    net = iface.network
    if not net or not iface.enabled or not iface.link_id:
        return False
    for rn in router.rip_networks:
        rn = rn.strip()
        try:
            if "/" in rn:
                rip_net = ipaddress.ip_network(rn, strict=False)
                if net.network_address in rip_net or net == rip_net:
                    return True
            else:
                if str(net.network_address) == rn:
                    return True
        except ValueError:
            continue
    return False


def discover_rip_neighbors(topology):
    """router_id -> list of (neighbor_router_id, local_iface_name, neighbor_iface_name)"""
    neighbors = {}
    rip_routers = [d for d in topology.nodes.values() if d.kind == "router" and d.rip_enabled]
    for router in rip_routers:
        neighbors[router.id] = []
        for name, iface in router.interfaces.items():
            if not _iface_in_rip(router, iface):
                continue
            endpoints, _hops = topology.flood_bfs(router.id, name)
            for edev_id, eiface_name in endpoints:
                edev = topology.nodes[edev_id]
                if edev.kind == "router" and edev.rip_enabled and edev.id != router.id:
                    if _iface_in_rip(edev, edev.interfaces[eiface_name]):
                        neighbors[router.id].append((edev.id, name, eiface_name))
    return neighbors


def converge_rip(topology, max_rounds=20):
    """Run distance-vector convergence across all RIP-enabled routers.
    Returns (log_lines, rounds_taken, neighbor_map)."""
    neighbors = discover_rip_neighbors(topology)
    routers = {rid: topology.nodes[rid] for rid in neighbors}
    log = []

    for r in routers.values():
        r.routing_table = [rt for rt in r.routing_table if rt.source != "R"]

    if not routers:
        return ["No RIP-enabled routers with matching `network` statements were found."], 0, neighbors

    changed = True
    rounds = 0
    while changed and rounds < max_rounds:
        changed = False
        rounds += 1
        snapshots = {rid: list(r.routing_table) for rid, r in routers.items()}

        for rid, nbrs in neighbors.items():
            router = routers[rid]
            for (neighbor_id, local_iface, neighbor_iface) in nbrs:
                neighbor = routers[neighbor_id]
                for nr in snapshots[neighbor_id]:
                    if nr.source not in ("C", "R"):
                        continue
                    if nr.iface == neighbor_iface:
                        continue  # split horizon
                    new_metric = nr.metric + 1
                    if new_metric >= 16:
                        continue  # RIP infinity
                    next_hop_ip = neighbor.interfaces[neighbor_iface].ip
                    existing = next((rt for rt in router.routing_table if rt.network == nr.network), None)
                    if existing is None:
                        router.routing_table.append(
                            Route(nr.network, "R", next_hop=next_hop_ip, iface=local_iface, metric=new_metric)
                        )
                        changed = True
                        log.append(f"{router.name}: learned {nr.network} via RIP from {neighbor.name} "
                                   f"(metric {new_metric}, next-hop {next_hop_ip})")
                    elif existing.source == "R" and new_metric < existing.metric:
                        existing.metric = new_metric
                        existing.next_hop = next_hop_ip
                        existing.iface = local_iface
                        changed = True
                        log.append(f"{router.name}: better route to {nr.network} via RIP from {neighbor.name} "
                                   f"(metric {new_metric})")
                    # existing.source == 'C' -> never override a directly connected route

    if not log:
        log.append("RIP neighbors found, but no new routes were exchanged (already converged, or no networks advertised).")
    log.append(f"Converged after {rounds} round(s).")
    return log, rounds, neighbors
