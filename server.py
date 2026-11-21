"""PSX 3D Sandbox — Modern Multiplayer Server (aiohttp + WebSocket).

Features:
- WebSocket for real-time player sync
- REST API for room management
- Auto-restart on Render.com
- Zero-downtime deployments
"""
import json
import time
import uuid
import asyncio
from dataclasses import dataclass, field, asdict
from typing import Optional
from aiohttp import web, WSMsgType
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERSION = "3.18"


@dataclass
class Player:
    """Connected player state."""
    id: str
    nick: str
    room: str
    pos: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    yaw: float = 0.0
    pitch: float = 0.0
    appearance: dict = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nick": self.nick,
            "room": self.room,
            "pos": self.pos,
            "yaw": self.yaw,
            "pitch": self.pitch,
            "appearance": self.appearance,
        }


@dataclass
class Room:
    """Game room with players."""
    name: str
    players: dict[str, Player] = field(default_factory=dict)
    max_players: int = 16
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "players": [p.to_dict() for p in self.players.values()],
            "player_count": len(self.players),
            "max_players": self.max_players,
            "created": self.created,
        }


class GameServer:
    """Main server managing rooms and WebSocket connections."""

    def __init__(self):
        self.rooms: dict[str, Room] = {}
        self.ws_connections: dict[str, web.WebSocketResponse] = {}
        self.player_rooms: dict[str, str] = {}  # player_id -> room_name

    def get_or_create_room(self, name: str) -> Room:
        if name not in self.rooms:
            self.rooms[name] = Room(name=name)
            logger.info(f"Room created: {name}")
        return self.rooms[name]

    def remove_player(self, player_id: str):
        """Remove player from room and cleanup."""
        room_name = self.player_rooms.pop(player_id, None)
        if room_name and room_name in self.rooms:
            room = self.rooms[room_name]
            if player_id in room.players:
                del room.players[player_id]
                logger.info(f"Player {player_id} left room {room_name}")
                # Broadcast leave event
                self.broadcast_to_room(room_name, {
                    "type": "player_leave",
                    "id": player_id,
                }, exclude=player_id)
            # Remove empty rooms
            if not room.players:
                del self.rooms[room_name]
                logger.info(f"Room {room_name} removed (empty)")

        if player_id in self.ws_connections:
            del self.ws_connections[player_id]

    def broadcast_to_room(self, room_name: str, message: dict, exclude: str = None):
        """Send message to all players in room."""
        if room_name not in self.rooms:
            return
        msg_json = json.dumps(message)
        room = self.rooms[room_name]
        for player_id, ws in list(self.ws_connections.items()):
            if player_id in room.players and player_id != exclude:
                try:
                    asyncio.create_task(ws.send_str(msg_json))
                except Exception as e:
                    logger.error(f"Send error to {player_id}: {e}")

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "version": VERSION,
            "rooms": len(self.rooms),
            "players": sum(len(r.players) for r in self.rooms.values()),
        })

    async def handle_rooms(self, request: web.Request) -> web.Response:
        """GET /api/rooms — List all rooms."""
        rooms_list = [r.to_dict() for r in self.rooms.values()]
        return web.json_response({
            "rooms": rooms_list,
            "total": len(rooms_list),
        })

    async def handle_create_room(self, request: web.Request) -> web.Response:
        """POST /api/rooms — Create new room."""
        try:
            data = await request.json()
            name = data.get("name", "Новая комната")[:50]
            max_players = int(data.get("max_players", 16))
            max_players = max(2, min(32, max_players))

            room = self.get_or_create_room(name)
            room.max_players = max_players
            return web.json_response(room.to_dict())
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket endpoint /ws — Real-time player sync."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        player_id = None
        room_name = None

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type", "")

                    # Join room
                    if msg_type == "join":
                        player_id = data.get("player_id", str(uuid.uuid4()))
                        nick = data.get("nick", "Игрок")[:20]
                        room_name = data.get("room", "Общая")[:50]
                        appearance = data.get("appearance", {})

                        room = self.get_or_create_room(room_name)

                        # Check room capacity
                        if len(room.players) >= room.max_players:
                            await ws.send_json({
                                "type": "join_error",
                                "message": "Комната заполнена",
                            })
                            continue

                        player = Player(
                            id=player_id,
                            nick=nick,
                            room=room_name,
                            appearance=appearance,
                        )
                        room.players[player_id] = player
                        self.ws_connections[player_id] = ws
                        self.player_rooms[player_id] = room_name

                        # Send welcome with current players
                        await ws.send_json({
                            "type": "welcome",
                            "id": player_id,
                            "room": room.to_dict(),
                            "players": [p.to_dict() for p in room.players.values() if p.id != player_id],
                            "spawn": [0.0, 0.0, 7.5, 0.0],
                            "history": [
                                {
                                    "nick": p.nick,
                                    "text": f"{p.nick} присоединился",
                                    "system": True,
                                }
                                for p in room.players.values()
                                if p.id != player_id
                            ][:10],
                        })

                        # Broadcast join
                        self.broadcast_to_room(room_name, {
                            "type": "player_join",
                            "player": player.to_dict(),
                        }, exclude=player_id)

                        logger.info(f"Player {nick} joined {room_name}")

                    # Position update
                    elif msg_type == "pos" and player_id:
                        if player_id in self.player_rooms:
                            room_name = self.player_rooms[player_id]
                            if room_name in self.rooms:
                                player = self.rooms[room_name].players.get(player_id)
                                if player:
                                    player.pos = data.get("pos", player.pos)
                                    player.yaw = data.get("yaw", player.yaw)
                                    player.pitch = data.get("pitch", player.pitch)
                                    player.last_seen = time.time()

                                    # Broadcast to room
                                    self.broadcast_to_room(room_name, {
                                        "type": "pos",
                                        "player_id": player_id,
                                        "pos": player.pos,
                                        "yaw": player.yaw,
                                        "pitch": player.pitch,
                                    }, exclude=player_id)

                    # Chat message
                    elif msg_type == "chat" and player_id:
                        if player_id in self.player_rooms:
                            room_name = self.player_rooms[player_id]
                            player = self.rooms[room_name].players.get(player_id)
                            if player:
                                text = data.get("text", "")[:200]
                                self.broadcast_to_room(room_name, {
                                    "type": "chat",
                                    "nick": player.nick,
                                    "text": text,
                                })

                    # Leave room
                    elif msg_type == "leave" and player_id:
                        self.remove_player(player_id)
                        player_id = None
                        room_name = None
                        await ws.send_json({"type": "left"})

                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WS error: {ws.exception()}")
                    break

        except Exception as e:
            logger.error(f"WS handler error: {e}")
        finally:
            if player_id:
                self.remove_player(player_id)

        return ws

    def create_app(self) -> web.Application:
        """Create aiohttp application."""
        app = web.Application()
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/api/rooms", self.handle_rooms)
        app.router.add_post("/api/rooms", self.handle_create_room)
        app.router.add_get("/ws", self.handle_ws)
        return app


def main():
    """Start the server."""
    import os
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")

    server = GameServer()
    app = server.create_app()

    logger.info(f"Starting PSX Sandbox Server v{VERSION} on {host}:{port}")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
