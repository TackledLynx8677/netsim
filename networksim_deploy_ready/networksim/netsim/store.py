import random
import re
import time

from .cli import CLI
from .models import Topology

ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I to avoid mix-ups
MAX_ROOMS = 300
IDLE_SECONDS = 6 * 3600


class Room:
    def __init__(self, room_id):
        self.id = room_id
        self.last_used = time.time()
        self.reset()

    def reset(self):
        self.topology = Topology()
        self.cli = CLI(self.topology)


rooms = {}      # room id -> Room
aliases = {}    # short join code -> room id


def get_room(room_id, active_ids=()):
    """Return the room for this id, creating it on first use. Returns None if
    the server is full. `active_ids` are rooms with people connected now."""
    room = rooms.get(room_id)
    if room is None:
        if len(rooms) >= MAX_ROOMS:
            _prune(active_ids)
            if len(rooms) >= MAX_ROOMS:
                return None
        room = rooms[room_id] = Room(room_id)
    room.last_used = time.time()
    return room


def _prune(active_ids):
    now = time.time()
    for rid in [r for r, room in rooms.items()
                if r not in active_ids and now - room.last_used > IDLE_SECONDS]:
        del rooms[rid]
    for code in [c for c, rid in aliases.items() if rid not in rooms]:
        del aliases[code]


def share_code(room_id):
    """The join code for a room (made on first use, then reused)."""
    for code, rid in aliases.items():
        if rid == room_id:
            return code
    while True:
        code = "".join(random.choice(CODE_ALPHABET) for _ in range(6))
        if code not in aliases:
            aliases[code] = room_id
            return code


def unshare(room_id):
    for code in [c for c, rid in aliases.items() if rid == room_id]:
        del aliases[code]


def resolve_code(code):
    return aliases.get((code or "").strip().upper())


def _cable(t, a, a_if, b, b_if):
    link = t.add_link(a.id, b.id, a_if, b_if)
    return link


def _configure(dev, iface_name, ip, mask):
    iface = dev.interfaces[iface_name]
    iface.ip, iface.mask, iface.enabled = ip, mask, True
    if dev.kind == "router":
        dev.rebuild_connected_routes()


def load_demo(room):
    room.reset()
    t = room.topology

    r1 = t.add_device("router", "R1", 260, 120)
    r2 = t.add_device("router", "R2", 620, 120)
    sw1 = t.add_device("switch", "SW1", 120, 300)
    sw2 = t.add_device("switch", "SW2", 760, 300)
    pc1 = t.add_device("pc", "PC1", 40, 440)
    pc2 = t.add_device("pc", "PC2", 200, 440)
    pc3 = t.add_device("pc", "PC3", 680, 440)
    pc4 = t.add_device("pc", "PC4", 840, 440)

    _cable(t, r1, "GigabitEthernet0/0", r2, "GigabitEthernet0/0")
    _cable(t, r1, "GigabitEthernet0/1", sw1, "FastEthernet0/1")
    _cable(t, sw1, "FastEthernet0/2", pc1, "FastEthernet0")
    _cable(t, sw1, "FastEthernet0/3", pc2, "FastEthernet0")
    _cable(t, r2, "GigabitEthernet0/1", sw2, "FastEthernet0/1")
    _cable(t, sw2, "FastEthernet0/2", pc3, "FastEthernet0")
    _cable(t, sw2, "FastEthernet0/3", pc4, "FastEthernet0")

    _configure(r1, "GigabitEthernet0/0", "192.168.100.1", "255.255.255.252")
    _configure(r1, "GigabitEthernet0/1", "10.0.1.1", "255.255.255.0")
    _configure(r2, "GigabitEthernet0/0", "192.168.100.2", "255.255.255.252")
    _configure(r2, "GigabitEthernet0/1", "10.0.2.1", "255.255.255.0")

    pc1.interfaces["FastEthernet0"].ip = "10.0.1.10"
    pc1.interfaces["FastEthernet0"].mask = "255.255.255.0"
    pc1.interfaces["FastEthernet0"].enabled = True
    pc1.default_gateway = "10.0.1.1"

    pc2.interfaces["FastEthernet0"].ip = "10.0.1.11"
    pc2.interfaces["FastEthernet0"].mask = "255.255.255.0"
    pc2.interfaces["FastEthernet0"].enabled = True
    pc2.default_gateway = "10.0.1.1"

    pc3.interfaces["FastEthernet0"].ip = "10.0.2.10"
    pc3.interfaces["FastEthernet0"].mask = "255.255.255.0"
    pc3.interfaces["FastEthernet0"].enabled = True
    pc3.default_gateway = "10.0.2.1"

    pc4.interfaces["FastEthernet0"].ip = "10.0.2.11"
    pc4.interfaces["FastEthernet0"].mask = "255.255.255.0"
    pc4.interfaces["FastEthernet0"].enabled = True
    pc4.default_gateway = "10.0.2.1"

    r1.add_static_route("10.0.2.0", "255.255.255.0", "192.168.100.2")
    r2.add_static_route("10.0.1.0", "255.255.255.0", "192.168.100.1")

    return t
