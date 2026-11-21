# PSX 3D Sandbox — Multiplayer Server

## Локальный запуск:
```bash
pip install -r requirements.txt
python server.py
```
Сервер запустится на http://localhost:8080

## Деплой на Render.com:

1. Создай репозиторий на GitHub с этими файлами
2. Зайди на https://render.com
3. New → Web Service → Connect GitHub repo
4. Render автоматически прочитает render.yaml
5. Deploy!

URL сервера будет: `https://psx-sandbox-server.onrender.com`

## API:

### GET /health
Проверка здоровья сервера
```json
{"status": "ok", "version": "3.18", "rooms": 2, "players": 5}
```

### GET /api/rooms
Список комнат
```json
{
  "rooms": [
    {"name": "Общая", "players": [...], "player_count": 3, "max_players": 16}
  ],
  "total": 1
}
```

### POST /api/rooms
Создать комнату
```json
{"name": "Моя комната", "max_players": 8}
```

### WebSocket /ws
Реал-тайм синхронизация игроков

**Клиент → Сервер:**
- `join` — войти в комнату
- `pos` — обновить позицию
- `chat` — отправить сообщение
- `leave` — выйти

**Сервер → Клиент:**
- `welcome` — успешное подключение
- `player_joined` — новый игрок
- `player_left` — игрок вышел
- `pos` — обновление позиции
- `chat` — сообщение в чат
