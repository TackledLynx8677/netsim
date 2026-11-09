import re

from flask import request
from flask_socketio import SocketIO, join_room

from .store import ROOM_ID_RE

socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")

COLORS = ["#ffb13b", "#4fd1c5", "#f472b6", "#a78bfa", "#60a5fa", "#f87171", "#a3e635", "#fb923c"]
users = {}          # socket id -> {"id", "name", "color", "room"}
_guest_counter = {}  # room id -> guests so far in that room


def active_rooms():
    """Rooms that have somebody connected right now."""
    return {u["room"] for u in users.values()}


def _public_users(room):
    return [{k: u[k] for k in ("id", "name", "color")} for u in users.values() if u["room"] == room]


def _user_for_request():
    """The collaborator who made the current HTTP request (via X-Socket-Id)."""
    return users.get(request.headers.get("X-Socket-Id", ""))


# socket events 
@socketio.on("connect")
def on_connect():
    room = request.args.get("room", "")
    if not ROOM_ID_RE.match(room):
        return False   # refuse sockets that name no valid room
    join_room(room)
    n = _guest_counter[room] = _guest_counter.get(room, 0) + 1
    me = {"id": request.sid, "name": f"Guest {n}", "color": COLORS[(n - 1) % len(COLORS)], "room": room}
    users[request.sid] = me
    public_me = {k: me[k] for k in ("id", "name", "color")}
    socketio.emit("welcome", {"you": public_me, "users": _public_users(room)}, to=request.sid)
    socketio.emit("presence", {"users": _public_users(room)}, to=room, skip_sid=request.sid)


@socketio.on("disconnect")
def on_disconnect():
    me = users.pop(request.sid, None)
    if me:
        socketio.emit("presence", {"users": _public_users(me["room"])}, to=me["room"])
        socketio.emit("cursor_gone", {"id": request.sid}, to=me["room"])


@socketio.on("set_name")
def on_set_name(data):
    me = users.get(request.sid)
    name = str((data or {}).get("name", "")).strip()[:20]
    if me and name:
        me["name"] = name
        socketio.emit("presence", {"users": _public_users(me["room"])}, to=me["room"])


@socketio.on("cursor")
def on_cursor(data):
    me = users.get(request.sid)
    if me:
        socketio.emit("cursor", {"id": request.sid, "x": data.get("x"), "y": data.get("y")},
                      to=me["room"], skip_sid=request.sid)


@socketio.on("drag")
def on_drag(data):
    me = users.get(request.sid)
    if me:
        socketio.emit("drag", {"id": data.get("id"), "x": data.get("x"), "y": data.get("y"),
                               "by": request.sid}, to=me["room"], skip_sid=request.sid)


#    broadcasting REST changes 
_ACTIVITY = [
    ("POST", r"^/api/devices$", "added a device"),
    ("DELETE", r"^/api/devices/", "deleted a device"),
    ("PUT", r"^/api/devices/[^/]+/rename$", "renamed a device"),
    ("POST", r"^/api/links$", "connected two devices"),
    ("DELETE", r"^/api/links/", "removed a cable"),
    ("POST", r"^/api/cli/", "ran a console command"),
    ("POST", r"^/api/simulate/ping$", "sent a ping"),
    ("POST", r"^/api/simulate/dhcp$", "requested an address via DHCP"),
    ("POST", r"^/api/rip/converge$", "ran RIP convergence"),
    ("POST", r"^/api/demo$", "loaded the demo topology"),
    ("POST", r"^/api/reset$", "cleared the canvas"),
]


def _describe(method, path):
    for m, pattern, text in _ACTIVITY:
        if method == m and re.search(pattern, path):
            return text
    return None


def after_api_request(response, room):
    """Called from Flask's after_request. Pushes changes to everyone else."""
    if not request.path.startswith("/api/") or request.method not in ("POST", "PUT", "DELETE"):
        return response
    if request.path.startswith("/api/room/"):   # sharing a code changes nothing on the canvas
        return response
    if response.status_code >= 400:
        return response

    me = _user_for_request()
    skip = request.headers.get("X-Socket-Id") or None
    by = me["name"] if me else "Someone"
    color = me["color"] if me else None

    if request.path in ("/api/simulate/ping", "/api/simulate/dhcp"):
        data = response.get_json(silent=True) or {}
        label = "ping" if request.path.endswith("ping") else "DHCP request"
        socketio.emit("sim_play", {
            "steps": data.get("steps", []), "success": data.get("success"),
            "error": data.get("error"), "lease": data.get("lease"),
            "label": label, "by": by, "color": color,
        }, to=room.id, skip_sid=skip)

    socketio.emit("topology", {
        "topology": room.topology.to_dict(), "by": by, "color": color,
        "activity": _describe(request.method, request.path),
        "reset": request.path in ("/api/reset", "/api/demo"),
    }, to=room.id, skip_sid=skip)
    return response
