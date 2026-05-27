# server.py
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI(title="War Room Console Server")

# Зберігаємо список усіх активних підключень (браузерів)
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.tactical_targets = [] # Тут сервер зберігатиме завантажені цілі

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # При підключенні відразу відправляємо існуючі цілі новому клієнту
        if self.tactical_targets:
            await websocket.send_text(json.dumps({"type": "init_targets", "payload": self.tactical_targets}))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# Віддаємо наш HTML інтерфейс при заході на головну сторінку
@app.get("/")
async def get_dashboard():
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# WebSocket-ендпоінт для реального часу
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Чекаємо дані від будь-якого клієнта (наприклад, імпорт KML)
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "new_targets":
                # Додаємо нові цілі в базу сервера
                new_targets = message.get("payload", [])
                manager.tactical_targets.extend(new_targets)
                
                # Розсилаємо оновлення ВСІМ підключеним клієнтам
                await manager.broadcast(json.dumps({
                    "type": "update_targets",
                    "payload": manager.tactical_targets
                }))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    print("🚀 Тактичний сервер запущено! Відкрийте http://localhost:8000 у браузері.")
    uvicorn.run(app, host="0.0.0.0", port=8000)