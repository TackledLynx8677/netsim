import os
from flask import g, Flask, jsonify, render_template, request

from netsim import collab, store
from netsim.cli import CLI
from netsim.dhcp import simulate_dhcp
from netsim.engine import simulate_echo
from netsim.routing import converge_rip

app = Flask(__name__)
collab.socketio.init_app(app)


@app.before_request
def pick_room():
    """Every API call says which room it belongs to (X-Room header). Browsers
    get a private, unguessable room id by default, so strangers never share."""
    if not request.path.startswith("/api/"):
        return None
    room_id = request.headers.get("X-Room", "")
    if not store.ROOM_ID_RE.match(room_id):
        return jsonify({"error": "missing or invalid room"}), 400
    g.room = store.get_room(room_id, collab.active_rooms())
    if g.room is None:
        return jsonify({"error": "server is full, try again later"}), 503
    return None


@app.after_request
def broadcast_changes(response):
    # push every successful API change to the OTHER people in the same room
    room = g.get("room")
    return collab.after_api_request(response, room) if room else response


def err(message, code=400):
    return jsonify({"error": message}), code


# Pages
@app.route("/")
def index():
    return render_template("index.html")



# Rooms (opt-in sharing)
@app.route("/api/room/share", methods=["POST"])
def room_share():
    return jsonify({"code": store.share_code(g.room.id)})


@app.route("/api/room/unshare", methods=["POST"])
def room_unshare():
    store.unshare(g.room.id)
    return jsonify({"ok": True})


@app.route("/api/room/resolve")
def room_resolve():
    room_id = store.resolve_code(request.args.get("code"))
    if not room_id or room_id not in store.rooms:
        return err("That code was not found. Check it and try again.", 404)
    return jsonify({"room": room_id})



# Topology
@app.route("/api/topology")
def get_topology():
    return jsonify(g.room.topology.to_dict())


@app.route("/api/reset", methods=["POST"])
def reset_topology():
    g.room.reset()
    return jsonify(g.room.topology.to_dict())


@app.route("/api/demo", methods=["POST"])
def load_demo():
    store.load_demo(g.room)
    return jsonify(g.room.topology.to_dict())



# Devices
@app.route("/api/devices", methods=["POST"])
def add_device():
    data = request.get_json(force=True) or {}
    kind = data.get("kind")
    name = data.get("name") or _next_name(kind)
    x, y = data.get("x", 100), data.get("y", 100)
    if kind not in ("router", "switch", "pc", "server"):
        return err(f"Unknown device kind '{kind}'")
    dev = g.room.topology.add_device(kind, name, x, y)
    return jsonify(dev.to_dict())


def _next_name(kind):
    prefix = {"router": "R", "switch": "SW", "pc": "PC", "server": "SRV"}.get(kind, "DEV")
    existing = {d.name for d in g.room.topology.nodes.values()}
    i = 1
    while f"{prefix}{i}" in existing:
        i += 1
    return f"{prefix}{i}"


@app.route("/api/devices/<dev_id>")
def get_device(dev_id):
    dev = g.room.topology.nodes.get(dev_id)
    if not dev:
        return err("Device not found", 404)
    return jsonify(dev.to_dict())


@app.route("/api/devices/<dev_id>/position", methods=["PUT"])
def move_device(dev_id):
    dev = g.room.topology.nodes.get(dev_id)
    if not dev:
        return err("Device not found", 404)
    data = request.get_json(force=True) or {}
    dev.x = data.get("x", dev.x)
    dev.y = data.get("y", dev.y)
    return jsonify(dev.to_dict())


@app.route("/api/devices/<dev_id>/rename", methods=["PUT"])
def rename_device(dev_id):
    dev = g.room.topology.nodes.get(dev_id)
    if not dev:
        return err("Device not found", 404)
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return err("Name cannot be empty")
    g.room.topology.rename_device(dev_id, name)
    return jsonify(dev.to_dict())


@app.route("/api/devices/<dev_id>", methods=["DELETE"])
def delete_device(dev_id):
    if dev_id not in g.room.topology.nodes:
        return err("Device not found", 404)
    g.room.topology.remove_device(dev_id)
    return jsonify({"deleted": dev_id})



# Links
@app.route("/api/links", methods=["POST"])
def add_link():
    data = request.get_json(force=True) or {}
    a_id, b_id = data.get("a_id"), data.get("b_id")
    a_iface, b_iface = data.get("a_iface"), data.get("b_iface")
    if not a_id or not b_id:
        return err("a_id and b_id are required")
    if a_id == b_id:
        return err("Cannot link a device to itself")
    try:
        link = g.room.topology.add_link(a_id, b_id, a_iface, b_iface)
    except ValueError as e:
        return err(str(e))
    return jsonify(link.to_dict())


@app.route("/api/links/<link_id>", methods=["DELETE"])
def delete_link(link_id):
    if link_id not in g.room.topology.links:
        return err("Link not found", 404)
    g.room.topology.remove_link(link_id)
    return jsonify({"deleted": link_id})



# CLI
@app.route("/api/cli/<dev_id>", methods=["POST"])
def run_cli(dev_id):
    if dev_id not in g.room.topology.nodes:
        return err("Device not found", 404)
    data = request.get_json(force=True) or {}
    command = data.get("command", "")
    result = g.room.cli.execute(dev_id, command)
    return jsonify(result)


@app.route("/api/cli/<dev_id>/prompt")
def cli_prompt(dev_id):
    dev = g.room.topology.nodes.get(dev_id)
    if not dev:
        return err("Device not found", 404)
    return jsonify({"prompt": g.room.cli.prompt(dev), "mode": dev.cli_mode})



# Simulation
@app.route("/api/simulate/ping", methods=["POST"])
def simulate_ping():
    data = request.get_json(force=True) or {}
    src_id, dst_ip = data.get("src_id"), data.get("dst_ip")
    if src_id not in g.room.topology.nodes:
        return err("Source device not found", 404)
    if not dst_ip:
        return err("dst_ip is required")
    result = simulate_echo(g.room.topology, src_id, dst_ip)
    return jsonify(result)


@app.route("/api/simulate/dhcp", methods=["POST"])
def simulate_dhcp_route():
    data = request.get_json(force=True) or {}
    pc_id = data.get("pc_id")
    if pc_id not in g.room.topology.nodes:
        return err("Device not found", 404)
    return jsonify(simulate_dhcp(g.room.topology, pc_id))


@app.route("/api/rip/converge", methods=["POST"])
def rip_converge():
    log, rounds, neighbors = converge_rip(g.room.topology)
    return jsonify({
        "log": log,
        "rounds": rounds,
        "neighbor_count": sum(len(v) for v in neighbors.values()),
        "topology": g.room.topology.to_dict(),
    })


if __name__ == "__main__":
    # local development. Hosting uses gunicorn instead (see Procfile).
    port = int(os.environ.get("PORT", 5050))
    collab.socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)
