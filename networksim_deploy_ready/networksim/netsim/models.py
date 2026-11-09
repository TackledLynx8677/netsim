"""
Core topology data model: Interfaces, Devices (Router / Switch / PC), Links,
and the Topology graph that ties them together.
This module intentionally mirrors real networking concepts so the simulation
engine (engine.py) can behave the way a real network would
"""
import ipaddress
import itertools
import uuid

_mac_counter = itertools.count(1)


def generate_mac():
    """Generate a Cisco-style dotted MAC address, e.g. 0c29.4a1b.0003"""
    n = next(_mac_counter)
    raw = 0x000C29000000 + n
    hexs = f"{raw:012x}"
    return f"{hexs[0:4]}.{hexs[4:8]}.{hexs[8:12]}"


def short_id():
    return uuid.uuid4().hex[:8]


class Interface:
    """A single network interface on a device."""

    def __init__(self, name, device_id):
        self.name = name
        self.device_id = device_id
        self.mac = generate_mac()
        self.ip = None
        self.mask = None
        self.enabled = False   # administrative state ("no shutdown")
        self.link_id = None    # id of the Link plugged into this port

    @property
    def status(self):
        """Real IOS reports two things: line protocol + admin status.
        I collapse that into a simple tri-state for the UI."""
        if not self.enabled:
            return "administratively down"
        if not self.link_id:
            return "down (no cable)"
        return "up"

    @property
    def network(self):
        if not self.ip or not self.mask:
            return None
        try:
            return ipaddress.ip_network(f"{self.ip}/{self.mask}", strict=False)
        except ValueError:
            return None

    def to_dict(self):
        return {
            "name": self.name,
            "mac": self.mac,
            "ip": self.ip,
            "mask": self.mask,
            "enabled": self.enabled,
            "link_id": self.link_id,
            "status": self.status,
        }


class Route:
    """A single routing table entry."""

    def __init__(self, network, source, next_hop=None, iface=None, metric=0):
        self.network = network  # ipaddress.ip_network
        self.source = source    # 'C' connected, 'S' static, 'R' RIP, 'S*' default
        self.next_hop = next_hop
        self.iface = iface
        self.metric = metric

    def to_dict(self):
        return {
            "network": str(self.network),
            "source": self.source,
            "next_hop": self.next_hop,
            "iface": self.iface,
            "metric": self.metric,
        }


class Device:
    """Base class for all network devices."""

    kind = "device"
    IFACE_PREFIX = "Eth"
    IFACE_COUNT = 1

    def __init__(self, name, x=0, y=0, dev_id=None):
        self.id = dev_id or short_id()
        self.name = name
        self.x = x
        self.y = y
        self.interfaces = {}
        self.arp_table = {}   # ip -> {mac, iface, type}
        self._make_interfaces()

        # CLI session state
        self.cli_mode = "user"    # user | priv | config | config-if | config-router
        self.current_iface = None
        self.priv_enabled = False
        self.rip_enabled = False
        self.rip_networks = set()  # classful/CIDR networks advertised

    def _make_interfaces(self):
        raise NotImplementedError

    def get_interface(self, name):
        return self.interfaces.get(name)

    def resolve_iface_name(self, token):
        """Allow IOS-style abbreviations, e.g. 'g0/0' -> 'GigabitEthernet0/0'."""
        token = token.strip().lower()
        for full in self.interfaces:
            if full.lower() == token:
                return full
        for full in self.interfaces:
            if full.lower().startswith(token):
                return full
        # try matching the numeric suffix only, e.g. "0/0"
        for full in self.interfaces:
            if full.lower().endswith(token):
                return full
        return None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.kind,
            "x": self.x,
            "y": self.y,
            "interfaces": {n: i.to_dict() for n, i in self.interfaces.items()},
        }


class Router(Device):
    kind = "router"
    IFACE_PREFIX = "GigabitEthernet0/"
    IFACE_COUNT = 4

    def _make_interfaces(self):
        for i in range(self.IFACE_COUNT):
            name = f"{self.IFACE_PREFIX}{i}"
            self.interfaces[name] = Interface(name, self.id)
        self.routing_table = []   # list[Route]

    def connected_routes(self):
        return [r for r in self.routing_table if r.source == "C"]

    def rebuild_connected_routes(self):
        """Recompute the 'C' (directly connected) routes from live interface state."""
        self.routing_table = [r for r in self.routing_table if r.source != "C"]
        for iface in self.interfaces.values():
            if iface.enabled and iface.link_id and iface.network:
                net = iface.network.network_address
                route = Route(
                    ipaddress.ip_network(f"{net}/{iface.mask}", strict=False),
                    "C", next_hop=None, iface=iface.name, metric=0,
                )
                self.routing_table.append(route)

    def add_static_route(self, network, mask, next_hop):
        net = ipaddress.ip_network(f"{network}/{mask}", strict=False)
        self.routing_table = [
            r for r in self.routing_table
            if not (r.source == "S" and r.network == net)
        ]
        self.routing_table.append(Route(net, "S", next_hop=next_hop, metric=1))

    def remove_static_route(self, network, mask):
        net = ipaddress.ip_network(f"{network}/{mask}", strict=False)
        self.routing_table = [
            r for r in self.routing_table
            if not (r.source == "S" and r.network == net)
        ]

    def lookup_route(self, dst_ip):
        """Longest-prefix-match lookup, real routers do this in hardware (CAM/TCAM)."""
        ip = ipaddress.ip_address(dst_ip)
        best = None
        for r in self.routing_table:
            try:
                if ip in r.network:
                    if best is None or r.network.prefixlen > best.network.prefixlen:
                        best = r
            except (ValueError, TypeError):
                continue
        return best

    def to_dict(self):
        d = super().to_dict()
        d["routing_table"] = [r.to_dict() for r in self.routing_table]
        d["arp_table"] = self.arp_table
        d["rip_enabled"] = self.rip_enabled
        d["rip_networks"] = list(self.rip_networks)
        return d


class Switch(Device):
    kind = "switch"
    IFACE_PREFIX = "FastEthernet0/"
    IFACE_COUNT = 8

    def _make_interfaces(self):
        for i in range(1, self.IFACE_COUNT + 1):
            name = f"{self.IFACE_PREFIX}{i}"
            self.interfaces[name] = Interface(name, self.id)
            self.interfaces[name].enabled = True  # switchports are up by default
        self.mac_table = {}   # mac -> {'iface': name, 'age': int}

    def learn(self, mac, iface_name):
        self.mac_table[mac] = {"iface": iface_name}

    def lookup(self, mac):
        entry = self.mac_table.get(mac)
        return entry["iface"] if entry else None

    def to_dict(self):
        d = super().to_dict()
        d["mac_table"] = {m: v["iface"] for m, v in self.mac_table.items()}
        return d


class PC(Device):
    kind = "pc"
    IFACE_PREFIX = "FastEthernet"
    IFACE_COUNT = 1

    def _make_interfaces(self):
        name = "FastEthernet0"
        self.interfaces[name] = Interface(name, self.id)
        self.default_gateway = None
        self.dhcp_server = None  # IP of the server that leased us our address (None = static)

    def to_dict(self):
        d = super().to_dict()
        d["default_gateway"] = self.default_gateway
        d["dhcp_server"] = self.dhcp_server
        d["arp_table"] = self.arp_table
        return d


class Server(PC):
    """A pretend server. It behaves like a PC (one NIC, can be pinged) but
    can also run a DHCP service that hands out addresses from a pool."""
    kind = "server"

    def _make_interfaces(self):
        super()._make_interfaces()
        self.dhcp_enabled = False
        self.pool_start = None # first address of the pool, e.g. "10.0.1.100"
        self.pool_end = None  # last address of the pool
        self.pool_mask = None  # subnet mask handed to clients
        self.pool_gateway = None # default gateway handed to clients
        self.leases = {}  # client MAC -> leased IP

    def to_dict(self):
        d = super().to_dict()
        d["dhcp"] = {
            "enabled": self.dhcp_enabled,
            "pool_start": self.pool_start, "pool_end": self.pool_end,
            "mask": self.pool_mask, "gateway": self.pool_gateway,
            "leases": dict(self.leases),
        }
        return d


class Link:
    """A physical cable between two device interfaces."""

    def __init__(self, a_device, a_iface, b_device, b_iface, link_id=None):
        self.id = link_id or short_id()
        self.a_device = a_device
        self.a_iface = a_iface
        self.b_device = b_device
        self.b_iface = b_iface

    def other_end(self, device_id, iface_name):
        if self.a_device == device_id and self.a_iface == iface_name:
            return self.b_device, self.b_iface
        if self.b_device == device_id and self.b_iface == iface_name:
            return self.a_device, self.a_iface
        return None, None

    def endpoints(self):
        return (self.a_device, self.a_iface), (self.b_device, self.b_iface)

    def to_dict(self):
        return {
            "id": self.id,
            "a_device": self.a_device, "a_iface": self.a_iface,
            "b_device": self.b_device, "b_iface": self.b_iface,
        }


class Topology:
    """The network graph: devices are nodes, links are edges."""

    def __init__(self):
        self.nodes = {}   # id -> Device
        self.links = {}   # id -> Link

    # device management 
    def add_device(self, kind, name, x=0, y=0):
        cls = {"router": Router, "switch": Switch, "pc": PC, "server": Server}.get(kind)
        if not cls:
            raise ValueError(f"Unknown device kind: {kind}")
        dev = cls(name, x, y)
        self.nodes[dev.id] = dev
        return dev

    def remove_device(self, dev_id):
        for link_id in [lid for lid, l in self.links.items()
                        if l.a_device == dev_id or l.b_device == dev_id]:
            self.remove_link(link_id)
        self.nodes.pop(dev_id, None)

    def rename_device(self, dev_id, new_name):
        if dev_id in self.nodes:
            self.nodes[dev_id].name = new_name

    # link management 
    def first_free_iface(self, dev_id):
        dev = self.nodes[dev_id]
        for name, iface in dev.interfaces.items():
            if iface.link_id is None:
                return name
        return None

    def add_link(self, a_id, b_id, a_iface=None, b_iface=None):
        dev_a, dev_b = self.nodes.get(a_id), self.nodes.get(b_id)
        if not dev_a or not dev_b:
            raise ValueError("Both devices must exist")
        a_iface = a_iface or self.first_free_iface(a_id)
        b_iface = b_iface or self.first_free_iface(b_id)
        if not a_iface or not b_iface:
            raise ValueError("No free interface available on one of the devices")
        if dev_a.interfaces[a_iface].link_id:
            raise ValueError(f"{dev_a.name} {a_iface} is already connected")
        if dev_b.interfaces[b_iface].link_id:
            raise ValueError(f"{dev_b.name} {b_iface} is already connected")

        link = Link(a_id, a_iface, b_id, b_iface)
        self.links[link.id] = link
        dev_a.interfaces[a_iface].link_id = link.id
        dev_b.interfaces[b_iface].link_id = link.id

        # Switch ports come up automatically once cabled (real switches actually do this)
        for dev, ifname in ((dev_a, a_iface), (dev_b, b_iface)):
            if dev.kind == "switch":
                dev.interfaces[ifname].enabled = True

        if dev_a.kind == "router":
            dev_a.rebuild_connected_routes()
        if dev_b.kind == "router":
            dev_b.rebuild_connected_routes()
        return link

    def remove_link(self, link_id):
        link = self.links.pop(link_id, None)
        if not link:
            return
        for dev_id, ifname in (link.endpoints()):
            dev = self.nodes.get(dev_id)
            if dev and ifname in dev.interfaces:
                dev.interfaces[ifname].link_id = None
        for dev_id in (link.a_device, link.b_device):
            dev = self.nodes.get(dev_id)
            if dev and dev.kind == "router":
                dev.rebuild_connected_routes()

    def link_for(self, dev_id, iface_name):
        dev = self.nodes.get(dev_id)
        if not dev:
            return None
        iface = dev.interfaces.get(iface_name)
        if not iface or not iface.link_id:
            return None
        return self.links.get(iface.link_id)

    #  L2 broadcast-domain discovery (used by ARP + RIP neighbor discovery) 
    def flood_bfs(self, start_dev_id, start_iface_name):
        """
        Flood outward from a starting interface through switches only
        (switches are transparent at L2; routers/PCs are broadcast-domain
        boundaries), the way a real broadcast/unknown-unicast frame would.
        Returns:
          endpoints: list of (device_id, iface_name) for every non-switch
                     device reachable in this broadcast domain
          hops: ordered list of dicts describing each link traversal, in
                the order the flood reaches it - {link_id, from_device,
                from_iface, to_device, to_iface} - used to animate the
                flood and to let switches learn the source MAC.
        """
        link = self.link_for(start_dev_id, start_iface_name)
        if not link:
            return [], []

        endpoints = []
        hops = []
        visited_links = {link.id}
        first_peer, first_iface = link.other_end(start_dev_id, start_iface_name)
        queue = [(first_peer, first_iface, start_dev_id, start_iface_name, link.id)]

        while queue:
            dev_id, iface_name, from_dev, from_iface, link_id = queue.pop(0)
            hops.append({
                "link_id": link_id,
                "from_device": from_dev, "from_iface": from_iface,
                "to_device": dev_id, "to_iface": iface_name,
            })
            dev = self.nodes.get(dev_id)
            if not dev:
                continue
            iface = dev.interfaces.get(iface_name)
            if not iface or not iface.enabled:
                continue

            if dev.kind == "switch":
                for other_name, other_iface in dev.interfaces.items():
                    if other_name == iface_name:
                        continue
                    if not other_iface.enabled or not other_iface.link_id:
                        continue
                    if other_iface.link_id in visited_links:
                        continue
                    visited_links.add(other_iface.link_id)
                    nxt_link = self.links[other_iface.link_id]
                    nxt_peer, nxt_iface = nxt_link.other_end(dev.id, other_name)
                    queue.append((nxt_peer, nxt_iface, dev.id, other_name, other_iface.link_id))
            else:
                endpoints.append((dev_id, iface_name))

        return endpoints, hops

    def shortest_l2_path(self, a_dev_id, a_iface_name, b_dev_id, b_iface_name):
        """
        Breath First Search (BFS) for the unique switched path between two specific endpoints in
        the same broadcast domain (our topologies are loop-free, so this
        is unambiguous - just like a real switch's single spanning path).
        Returns an ordered list of hop dicts like flood_bfs, or [] if
        unreachable.
        """
        link = self.link_for(a_dev_id, a_iface_name)
        if not link:
            return []
        first_peer, first_iface = link.other_end(a_dev_id, a_iface_name)
        if first_peer == b_dev_id and first_iface == b_iface_name:
            return [{"link_id": link.id, "from_device": a_dev_id, "from_iface": a_iface_name,
                     "to_device": b_dev_id, "to_iface": b_iface_name}]

        visited_links = {link.id}
        start_hop = {"link_id": link.id, "from_device": a_dev_id, "from_iface": a_iface_name,
                     "to_device": first_peer, "to_iface": first_iface}
        queue = [(first_peer, first_iface, [start_hop])]

        while queue:
            dev_id, iface_name, path_so_far = queue.pop(0)
            if dev_id == b_dev_id and iface_name == b_iface_name:
                return path_so_far
            dev = self.nodes.get(dev_id)
            if not dev or dev.kind != "switch":
                continue
            iface = dev.interfaces.get(iface_name)
            if not iface or not iface.enabled:
                continue
            for other_name, other_iface in dev.interfaces.items():
                if other_name == iface_name:
                    continue
                if not other_iface.enabled or not other_iface.link_id:
                    continue
                if other_iface.link_id in visited_links:
                    continue
                visited_links.add(other_iface.link_id)
                nxt_link = self.links[other_iface.link_id]
                nxt_peer, nxt_iface = nxt_link.other_end(dev.id, other_name)
                hop = {"link_id": other_iface.link_id, "from_device": dev.id, "from_iface": other_name,
                       "to_device": nxt_peer, "to_iface": nxt_iface}
                queue.append((nxt_peer, nxt_iface, path_so_far + [hop]))
        return []

    def find_iface_owning_ip(self, ip):
        """Search the whole topology for a device interface configured with `ip`."""
        for dev in self.nodes.values():
            for name, iface in dev.interfaces.items():
                if iface.ip == ip and iface.enabled:
                    return dev, name
        return None, None

    #  serialization 
    def to_dict(self):
        return {
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "links": {k: v.to_dict() for k, v in self.links.items()},
        }
