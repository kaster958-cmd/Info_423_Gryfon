import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI(title="War Room Console Server")

# Клас керування WebSocket з'єднаннями союзників
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.tactical_targets = []  # Серверна база даних тактичних цілей

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # При підключенні нового союзника відразу відправляємо йому поточну базу цілей
        if self.tactical_targets:
            await websocket.send_text(json.dumps({
                "type": "init_targets", 
                "payload": self.tactical_targets
            }))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # Розсилаємо пакет даних усім активним союзникам у мережі
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                # Якщо з'єднання відвалилося, ігноруємо помилку, менеджер почистить його при дисконекті
                pass

manager = ConnectionManager()

# Роздача тактичного інтерфейсу index.html
@app.get("/")
async def get_dashboard():
    # На хмарному сервері index.html має лежати в тій самій папці
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    return HTMLResponse(content="<h3>Помилка: Файл index.html не знайдено на сервері.</h3>", status_code=404)

# WebSocket канал для обміну координатами в реальному часі
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Очікування трансляції від будь-кого з операторів
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "new_targets":
                new_targets = message.get("payload", [])
                
                # Об'єднуємо унікальні цілі на сервері, щоб уникнути дублів
                existing_ids = {t["id"] for t in manager.tactical_targets if "id" in t}
                for nt in new_targets:
                    if nt.get("id") not in existing_ids:
                        manager.tactical_targets.append(nt)
                
                # Миттєво надсилаємо оновлену базу цілей усім союзникам
                await manager.broadcast(json.dumps({
                    "type": "update_targets",
                    "payload": manager.tactical_targets
                }))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"Помилка сокету: {e}")
        manager.disconnect(websocket)

if __name__ == "__main__":
    # Хмарні сервери (Render, Heroku, Railway) призначають порт динамічно через змінну PORT.
    # Якщо змінна відсутня, використовуємо стандартний локальний порт 8000.
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Запуск тактичного сервера на порту {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)