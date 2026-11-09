class Packet:
    ETHERTYPE_IPV4 = "0x0800"
    ETHERTYPE_ARP = "0x0806"

    def __init__(self, app_data=None, transport=None, network=None, datalink=None):
        # Application layer: raw payload (e.g. "Echo Request" or user text)
        self.app = {"data": app_data} if app_data is not None else None
        # Transport layer: protocol/ports (omitted for ICMP, which rides directly on IP)
        self.transport = transport
        # Network layer: src/dst IP, ttl, protocol
        self.network = network
        # Data link layer: src/dst MAC, ethertype
        self.datalink = datalink

    def clone(self):
        p = Packet()
        p.app = dict(self.app) if self.app else None
        p.transport = dict(self.transport) if self.transport else None
        p.network = dict(self.network) if self.network else None
        p.datalink = dict(self.datalink) if self.datalink else None
        return p

    def snapshot(self):
        """A serializable snapshot of every header, for the event log / UI."""
        return {
            "application": self.app,
            "transport": self.transport,
            "network": self.network,
            "datalink": self.datalink,
        }

    @staticmethod
    def new_icmp_echo(src_ip, dst_ip, ttl=64, seq=1, data="Echo request payload"):
        p = Packet(app_data=data)
        p.network = {
            "protocol": "ICMP",
            "type": "Echo Request",
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "ttl": ttl,
            "seq": seq,
        }
        return p

    @staticmethod
    def new_icmp_reply(echo_packet, src_ip):
        p = Packet(app_data=echo_packet.app["data"] if echo_packet.app else "")
        p.network = {
            "protocol": "ICMP",
            "type": "Echo Reply",
            "src_ip": src_ip,
            "dst_ip": echo_packet.network["src_ip"],
            "ttl": 64,
            "seq": echo_packet.network.get("seq", 1),
        }
        return p

    @staticmethod
    def new_arp_request(src_ip, src_mac, target_ip):
        p = Packet()
        p.datalink = {
            "src_mac": src_mac, "dst_mac": "ffff.ffff.ffff",
            "ethertype": Packet.ETHERTYPE_ARP,
        }
        p.network = {
            "protocol": "ARP", "op": "request",
            "sender_ip": src_ip, "sender_mac": src_mac,
            "target_ip": target_ip, "target_mac": "00:00:00:00:00:00",
        }
        return p

    @staticmethod
    def new_arp_reply(src_ip, src_mac, target_ip, target_mac):
        p = Packet()
        p.datalink = {
            "src_mac": src_mac, "dst_mac": target_mac,
            "ethertype": Packet.ETHERTYPE_ARP,
        }
        p.network = {
            "protocol": "ARP", "op": "reply",
            "sender_ip": src_ip, "sender_mac": src_mac,
            "target_ip": target_ip, "target_mac": target_mac,
        }
        return p

    def set_frame(self, src_mac, dst_mac):
        self.datalink = {
            "src_mac": src_mac, "dst_mac": dst_mac,
            "ethertype": self.ETHERTYPE_IPV4,
        }
