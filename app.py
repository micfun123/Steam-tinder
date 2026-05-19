import os, re, random, string, threading
import requests
from dotenv import load_dotenv
from flask import Flask, render_template
from flask_socketio import SocketIO, emit, join_room

load_dotenv()
STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")

app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

rooms = {}  # code -> room state

# ── Steam helpers ──────────────────────────────────────────────────────────

def steam_get(path, params, timeout=15):
    r = requests.get(f"https://api.steampowered.com{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def resolve_id(api_key, raw):
    s = raw.strip()
    if re.match(r"^\d{17}$", s):
        return s
    m = re.search(r"steamcommunity\.com/id/([^/?#]+)", s)
    vanity = m.group(1) if m else s.strip("/")
    data = steam_get("/ISteamUser/ResolveVanityURL/v0001/", {"key": api_key, "vanityurl": vanity})
    if data["response"].get("success") == 1:
        return data["response"]["steamid"]
    raise ValueError(f'Cannot find Steam account: "{raw}"')

def fetch_summary(api_key, sid):
    data = steam_get("/ISteamUser/GetPlayerSummaries/v0002/", {"key": api_key, "steamids": sid})
    players = data.get("response", {}).get("players", [])
    return players[0] if players else {}

def fetch_games(api_key, sid):
    data = steam_get("/IPlayerService/GetOwnedGames/v0001/", {
        "key": api_key, "steamid": sid, "include_appinfo": 1, "format": "json"
    })
    return data.get("response", {}).get("games", [])

def gen_code():
    # Avoid ambiguous characters
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choices(chars, k=5))

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

# ── Socket events ──────────────────────────────────────────────────────────

@socketio.on("create_room")
def on_create(data):
    api_key = data.get("api_key", "").strip() or STEAM_API_KEY
    inputs   = [i.strip() for i in data.get("player_inputs", []) if i.strip()]

    if not api_key:
        emit("error", {"msg": "Steam API key is required"}); return
    if len(inputs) < 2:
        emit("error", {"msg": "Add at least 2 Steam profiles"}); return

    code = gen_code()
    rooms[code] = {
        "api_key": api_key,
        "player_inputs": inputs,
        "players": [],
        "games": [],
        "votes": {},
        "done": set(),
        "claimed": {},          # sid -> player_name
        "phase": "loading",
    }
    join_room(code)
    emit("room_created", {"code": code})

    def fetch():
        room = rooms[code]
        try:
            def status(msg, pct):
                socketio.emit("loading_status", {"msg": msg, "pct": pct}, to=code)

            status("Resolving Steam accounts…", 5)
            steam_ids = [resolve_id(api_key, inp) for inp in inputs]

            status("Fetching player profiles…", 18)
            summaries = [fetch_summary(api_key, sid) for sid in steam_ids]
            players = [
                {
                    "name":     s.get("personaname", f"Player {i+1}"),
                    "steam_id": steam_ids[i],
                    "avatar":   s.get("avatarmedium", ""),
                }
                for i, s in enumerate(summaries)
            ]

            libs = []
            for i, sid in enumerate(steam_ids):
                pct = 18 + int((i / len(steam_ids)) * 65)
                status(f"Loading {players[i]['name']}'s library…", pct)
                libs.append(fetch_games(api_key, sid))

            status("Finding shared games…", 88)
            sets = [set(g["appid"] for g in lib) for lib in libs]
            common_ids = sets[0].intersection(*sets[1:])

            game_map = {g["appid"]: g for g in libs[0]}
            pt_map = {}
            for lib in libs:
                for g in lib:
                    if g["appid"] in common_ids:
                        pt_map[g["appid"]] = pt_map.get(g["appid"], 0) + g.get("playtime_forever", 0)

            common = sorted(
                [game_map[aid] for aid in common_ids if aid in game_map],
                key=lambda g: pt_map.get(g["appid"], 0),
                reverse=True,
            )[:60]

            if not common:
                socketio.emit("error", {"msg": "No shared games found! Make sure everyone's Steam library is set to Public."}, to=code)
                return

            room["players"] = players
            room["games"]   = common
            room["votes"]   = {p["name"]: {} for p in players}
            room["phase"]   = "lobby"

            socketio.emit("setup_done", {
                "players":    players,
                "games":      common,
                "game_count": len(common),
            }, to=code)

        except Exception as e:
            socketio.emit("error", {"msg": str(e)}, to=code)

    threading.Thread(target=fetch, daemon=True).start()


@socketio.on("join_game")
def on_join(data):
    from flask import request as req
    code = data.get("code", "").upper().strip()
    if code not in rooms:
        emit("error", {"msg": "Room not found — check the code and try again"}); return

    join_room(code)
    room = rooms[code]
    emit("joined", {
        "phase":         room["phase"],
        "players":       room["players"],
        "games":         room["games"],
        "game_count":    len(room["games"]),
        "claimed_names": list(room["claimed"].values()),
    })


@socketio.on("claim_player")
def on_claim(data):
    from flask import request as req
    code        = data.get("code", "")
    player_name = data.get("player_name", "")
    room        = rooms.get(code)
    if not room: return

    # Don't let two sockets claim the same player
    taken_by_other = {
        name for sid, name in room["claimed"].items() if sid != req.sid
    }
    if player_name in taken_by_other:
        emit("error", {"msg": f"{player_name} is already taken"}); return

    room["claimed"][req.sid] = player_name
    socketio.emit("claim_update", {"claimed_names": list(room["claimed"].values())}, to=code)
    emit("claimed_ok", {"player_name": player_name})


@socketio.on("vote")
def on_vote(data):
    code        = data.get("code", "")
    player_name = data.get("player_name", "")
    appid       = data.get("appid")
    liked       = data.get("liked", False)
    room        = rooms.get(code)
    if not room or player_name not in room.get("votes", {}): return
    room["votes"][player_name][appid] = liked


@socketio.on("player_done")
def on_done(data):
    code        = data.get("code", "")
    player_name = data.get("player_name", "")
    room        = rooms.get(code)
    if not room: return

    room["done"].add(player_name)
    all_names = {p["name"] for p in room["players"]}

    socketio.emit("progress_update", {
        "done_players": list(room["done"]),
        "total":        len(all_names),
    }, to=code)

    if room["done"] >= all_names:
        votes   = room["votes"]
        matched = [
            g for g in room["games"]
            if all(votes.get(p["name"], {}).get(g["appid"]) is True for p in room["players"])
        ]
        socketio.emit("results", {
            "matched_games": matched,
            "votes":         {pn: dict(pv) for pn, pv in votes.items()},
            "games":         room["games"],
            "players":       room["players"],
        }, to=code)


@socketio.on("disconnect")
def on_disconnect():
    from flask import request as req
    # Remove from claimed in any room
    for room in rooms.values():
        room["claimed"].pop(req.sid, None)


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)