# server.py
import json
import os
import time
import uuid
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Header, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from docx import Document
import re
import io
import uvicorn
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

app = FastAPI(title="War Room Console Server")

# Зберігаємо список усіх активних підключень (браузерів)
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.tactical_targets = [] # Тут сервер зберігатиме завантажені цілі
        self.allowed_tokens: dict[str, float] = {}  # token -> expiry timestamp
        self._data_file = 'targets.json'
        self.load_targets()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # При підключенні відразу відправляємо існуючі цілі новому клієнту
        if self.tactical_targets:
            await websocket.send_text(json.dumps({"type": "init_targets", "payload": self.tactical_targets}))

    def load_targets(self):
        try:
            if os.path.exists(self._data_file):
                with open(self._data_file, 'r', encoding='utf-8') as f:
                    self.tactical_targets = json.load(f)
        except Exception:
            self.tactical_targets = []

    def save_targets(self):
        try:
            with open(self._data_file, 'w', encoding='utf-8') as f:
                json.dump(self.tactical_targets, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print('Error saving targets:', e)

    # Units (ORBAT) handling
    def load_units(self):
        self.units_file = 'units.json'
        units = []
        try:
            if os.path.exists(self.units_file):
                with open(self.units_file, 'r', encoding='utf-8') as f:
                    units = json.load(f)
        except Exception as e:
            print('Error loading units:', e)
            units = []
        self.units = units

    def save_units(self):
        try:
            with open(self.units_file, 'w', encoding='utf-8') as f:
                json.dump(self.units, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print('Error saving units:', e)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()
manager.load_units()

# Helper: validate token
def validate_token(auth_header: str | None) -> bool:
    if not auth_header:
        return False
    if not auth_header.startswith('Bearer '):
        return False
    token = auth_header.split(' ', 1)[1]
    expiry = manager.allowed_tokens.get(token)
    if not expiry:
        return False
    if time.time() > expiry:
        del manager.allowed_tokens[token]
        return False
    return True


# --- Simple parsers for .docx and .txt files to build unit objects ---

def _make_id(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9\-]+", '-', name.strip().lower())
    if not base:
        return str(uuid.uuid4())
    return base


def _parse_coord_pair(text: str):
    # find two floats in the text
    m = re.findall(r"-?\d+\.\d+", text)
    if len(m) >= 2:
        try:
            return float(m[0]), float(m[1])
        except:
            return None, None
    # try comma separated
    if ',' in text:
        parts = [p.strip() for p in text.split(',')]
        try:
            return float(parts[0]), float(parts[1])
        except:
            return None, None
    return None, None


def parse_docx_upload(upload: UploadFile):
    try:
        upload.file.seek(0)
        doc = Document(upload.file)
    except Exception as e:
        print('docx parse error:', e)
        return []
    paras = [p.text for p in doc.paragraphs]
    blocks = []
    cur = []
    for p in paras:
        if p.strip() == '':
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(p.strip())
    if cur:
        blocks.append(cur)

    units = []
    for block in blocks:
        unit = {}
        last_key = None
        for line in block:
            # bullet items
            if line.startswith('-') or line.startswith('•'):
                item = line.lstrip('-• ').strip()
                if last_key:
                    unit.setdefault(last_key, []).append(item)
                continue
            if ':' in line:
                k, v = line.split(':', 1)
                k = k.strip().lower()
                v = v.strip()
                last_key = None
                if k in ('name', 'назва', 'unit', 'wartość'):
                    unit['name'] = v
                    unit['id'] = _make_id(v)
                elif k in ('shortname','short name','скорочено'):
                    unit['shortName'] = v
                elif 'meta' in k or k in ('meta', 'опис'):
                    unit['meta'] = v
                elif 'threat' in k or k in ('threat','рівень загрози'):
                    unit['threat'] = v
                elif k in ('personnel','штатна чисельність','personel'):
                    unit['personnel'] = v
                elif k in ('frequencies','частот'):
                    unit['frequencies'] = v
                elif k in ('mainuav','main uav','основний засіб'):
                    unit['mainUav'] = v
                elif k in ('reb','рєб','рeб'):
                    unit['reb'] = v
                elif k in ('layer','шар'):
                    unit['layer'] = v
                elif k in ('lat','latitude','широта'):
                    try:
                        unit['lat'] = float(v)
                    except:
                        unit['lat'] = None
                elif k in ('lng','lon','longitude','довгота'):
                    try:
                        unit['lng'] = float(v)
                    except:
                        unit['lng'] = None
                elif 'coord' in k or 'коорд' in k or 'координат' in k:
                    lat, lng = _parse_coord_pair(v)
                    if lat is not None:
                        unit['lat'] = lat
                        unit['lng'] = lng
                elif k in ('origindesc','origin','походження'):
                    unit['originDesc'] = v
                    last_key = 'originDesc'
                elif k in ('tacticaldesc','tactical','тактика'):
                    unit['tacticalDesc'] = v
                    last_key = 'tacticalDesc'
                elif k in ('uavs','безпілотники','uav'):
                    # split by comma or semicolon
                    items = re.split(r'[;,]\s*|\n', v)
                    unit['uavs'] = [it.strip() for it in items if it.strip()]
                    last_key = 'uavs'
                elif k in ('commanders','командири'):
                    unit['commanders'] = [it.strip() for it in re.split(r'[;,]\s*', v) if it.strip()]
                    last_key = 'commanders'
                elif k in ('assets','засоби','майно'):
                    unit['assets'] = [it.strip() for it in re.split(r'[;,]\s*', v) if it.strip()]
                    last_key = 'assets'
                elif k in ('intercept','перехоплення'):
                    unit['intercept'] = v
                    last_key = 'intercept'
                else:
                    # unknown key -> store raw
                    unit[k] = v
                    last_key = k
            else:
                # continuation line
                if last_key:
                    prev = unit.get(last_key, '')
                    if isinstance(prev, list):
                        prev.append(line)
                    else:
                        unit[last_key] = (prev + '\n' + line).strip()
                else:
                    # try to fill name if missing
                    if 'name' not in unit:
                        unit['name'] = line
                        unit['id'] = _make_id(line)
        # ensure id
        if 'id' not in unit:
            if 'name' in unit:
                unit['id'] = _make_id(unit['name'])
            else:
                unit['id'] = str(uuid.uuid4())
        units.append(unit)
    return units


def parse_txt_content(text: str):
    parts = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    units = []
    for block in parts:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        # reuse simple parser logic
        fake_upload = type('X', (), {'file': None})()
        # emulate block parsing by joining lines with newlines and splitting by '\n'
        cur = []
        for l in lines:
            cur.append(l)
        # emulate parse on blocks list
        unit = {}
        last_key = None
        for line in cur:
            if line.startswith('-') or line.startswith('•'):
                item = line.lstrip('-• ').strip()
                if last_key:
                    unit.setdefault(last_key, []).append(item)
                continue
            if ':' in line:
                k, v = line.split(':', 1)
                k = k.strip().lower(); v = v.strip()
                last_key = None
                if k in ('name','назва'):
                    unit['name'] = v; unit['id'] = _make_id(v)
                elif k in ('uavs','безпілотники'):
                    unit['uavs'] = [it.strip() for it in re.split(r'[;,]\s*', v) if it.strip()]
                    last_key = 'uavs'
                else:
                    unit[k] = v; last_key = k
            else:
                if last_key:
                    prev = unit.get(last_key, '')
                    if isinstance(prev, list):
                        prev.append(line)
                    else:
                        unit[last_key] = (prev + '\n' + line).strip()
                else:
                    if 'name' not in unit:
                        unit['name'] = line; unit['id'] = _make_id(line)
        if 'id' not in unit:
            if 'name' in unit:
                unit['id'] = _make_id(unit['name'])
            else:
                unit['id'] = str(uuid.uuid4())
        units.append(unit)
    return units


def get_gdrive_service():
    service_account_file = os.environ.get('GDRIVE_CREDENTIALS_FILE', 'credentials.json')
    if not os.path.exists(service_account_file):
        raise FileNotFoundError(f"Google service account file {service_account_file} not found")
    scopes = ['https://www.googleapis.com/auth/drive.readonly']
    creds = service_account.Credentials.from_service_account_file(service_account_file, scopes=scopes)
    return build('drive', 'v3', credentials=creds)


def download_drive_file(service, file_id, mime_type):
    stream = io.BytesIO()
    if mime_type == 'application/vnd.google-apps.document':
        request = service.files().export_media(fileId=file_id, mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    else:
        request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(stream, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    stream.seek(0)
    return stream.read()


def parse_drive_file(service, drive_file):
    mime_type = drive_file.get('mimeType', '')
    file_id = drive_file.get('id')
    name = drive_file.get('name', 'file')
    if not file_id:
        return []
    data = download_drive_file(service, file_id, mime_type)
    if mime_type == 'text/plain' or name.lower().endswith('.txt'):
        try:
            text = data.decode('utf-8', errors='ignore')
        except Exception:
            text = data.decode('cp1251', errors='ignore')
        return parse_txt_content(text)
    if mime_type in ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.google-apps.document') or name.lower().endswith('.docx'):
        class F:
            def __init__(self, bytes_data):
                self.file = io.BytesIO(bytes_data)
                self.filename = name
        return parse_docx_upload(F(data))
    return []


def sync_units_from_gdrive(folder_id: str, include_subfolders: bool = False):
    if not folder_id:
        raise ValueError('Folder ID is required')
    service = get_gdrive_service()
    added = []
    existing_ids = {x.get('id') for x in getattr(manager, 'units', [])}

    def scan_folder(current_folder_id):
        query = f"'{current_folder_id}' in parents and trashed=false"
        response = service.files().list(
            q=query,
            pageSize=200,
            fields='files(id,name,mimeType)'
        ).execute()
        for drive_file in response.get('files', []):
            lower = drive_file.get('name', '').lower()
            mime_type = drive_file.get('mimeType', '')
            supported = mime_type in ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.google-apps.document', 'text/plain') or lower.endswith('.docx') or lower.endswith('.txt')
            if supported:
                units = parse_drive_file(service, drive_file)
                for u in units:
                    if u.get('id') in existing_ids:
                        continue
                    existing_ids.add(u.get('id'))
                    manager.units.append(u)
                    added.append(u)
            if include_subfolders and mime_type == 'application/vnd.google-apps.folder':
                scan_folder(drive_file['id'])

    scan_folder(folder_id)
    manager.save_units()
    return added


# Endpoint: upload .docx/.txt files and parse units
@app.post('/api/units/import')
async def api_import_units(files: list[UploadFile] = File(...), authorization: str | None = Header(default=None)):
    if not validate_token(authorization):
        raise HTTPException(status_code=401, detail='Unauthorized')
    added = []
    for f in files:
        name = f.filename or 'file'
        lower = name.lower()
        if lower.endswith('.docx'):
            units = parse_docx_upload(f)
        elif lower.endswith('.txt'):
            txt = (await f.read()).decode('utf-8', errors='ignore')
            units = parse_txt_content(txt)
        else:
            # skip unsupported
            units = []
        if units:
            for u in units:
                # avoid duplicates by id
                existing_ids = {x.get('id') for x in getattr(manager, 'units', [])}
                if u.get('id') in existing_ids:
                    # merge simple: skip
                    continue
                manager.units.append(u)
                added.append(u)
    manager.save_units()
    # broadcast units update
    await manager.broadcast(json.dumps({ 'type': 'update_units', 'payload': manager.units }))
    return JSONResponse({ 'status': 'ok', 'added': len(added), 'files': [f.filename for f in files] })


# Endpoint: parse all files in a server-side directory
@app.post('/api/units/import_dir')
async def api_import_units_dir(req: Request, authorization: str | None = Header(default=None)):
    if not validate_token(authorization):
        raise HTTPException(status_code=401, detail='Unauthorized')
    body = await req.json()
    path = body.get('path', 'units_docs')
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail='Invalid path')
    added = []
    for fname in os.listdir(path):
        full = os.path.join(path, fname)
        if not os.path.isfile(full):
            continue
        lower = fname.lower()
        try:
            if lower.endswith('.docx'):
                with open(full, 'rb') as fh:
                    # construct a fake UploadFile-like
                    class F:
                        def __init__(self, fh):
                            self.file = fh
                            self.filename = fname
                    units = parse_docx_upload(F(fh))
            elif lower.endswith('.txt'):
                with open(full, 'r', encoding='utf-8') as fh:
                    txt = fh.read()
                units = parse_txt_content(txt)
            else:
                units = []
            for u in units:
                existing_ids = {x.get('id') for x in getattr(manager, 'units', [])}
                if u.get('id') in existing_ids:
                    continue
                manager.units.append(u)
                added.append(u)
        except Exception as e:
            print('Error parsing', full, e)
    manager.save_units()
    await manager.broadcast(json.dumps({ 'type': 'update_units', 'payload': manager.units }))
    return JSONResponse({ 'status': 'ok', 'added': len(added), 'path': path })


@app.post('/api/units/gdrive_sync')
async def api_units_gdrive_sync(req: Request, authorization: str | None = Header(default=None)):
    if not validate_token(authorization):
        raise HTTPException(status_code=401, detail='Unauthorized')
    body = await req.json()
    folder_id = body.get('folder_id') or os.environ.get('GDRIVE_FOLDER_ID')
    include_subfolders = bool(body.get('include_subfolders', False))
    if not folder_id:
        raise HTTPException(status_code=400, detail='folder_id is required')
    try:
        added = sync_units_from_gdrive(folder_id, include_subfolders=include_subfolders)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    await manager.broadcast(json.dumps({ 'type': 'update_units', 'payload': manager.units }))
    return JSONResponse({ 'status': 'ok', 'added': len(added), 'count': len(manager.units), 'folder_id': folder_id })


# Віддаємо наш HTML інтерфейс при заході на головну сторінку
@app.get("/")
async def get_dashboard():
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)


# Проста авторизація: повертаємо токен при вірному паролі
@app.post('/api/login')
async def api_login(req: Request):
    body = await req.json()
    password = body.get('password')
    expected = os.environ.get('ADMIN_PASSWORD', 'admin123')
    if password == expected:
        token = str(uuid.uuid4())
        expiry = time.time() + 60 * 60  # 1 hour
        manager.allowed_tokens[token] = expiry
        return JSONResponse({ 'token': token, 'expiry': expiry })
    raise HTTPException(status_code=401, detail='Invalid credentials')


# API: отримати всі цілі (public)
@app.get('/api/targets')
async def api_get_targets():
    return JSONResponse({ 'count': len(manager.tactical_targets), 'data': manager.tactical_targets })


@app.get('/api/units')
async def api_get_units():
    units = getattr(manager, 'units', [])
    return JSONResponse({ 'count': len(units), 'data': units })


@app.post('/api/units')
async def api_post_units(req: Request, authorization: str | None = Header(default=None)):
    if not validate_token(authorization):
        raise HTTPException(status_code=401, detail='Unauthorized')
    body = await req.json()
    payload = body.get('payload') if isinstance(body, dict) else body
    if not payload or not isinstance(payload, list):
        raise HTTPException(status_code=400, detail='payload must be a list')
    manager.units = payload
    manager.save_units()
    return JSONResponse({ 'status': 'ok', 'count': len(manager.units) })


# API: додати цілі (потребує токена)
@app.post('/api/targets')
async def api_post_targets(req: Request, authorization: str | None = Header(default=None)):
    if not validate_token(authorization):
        raise HTTPException(status_code=401, detail='Unauthorized')
    body = await req.json()
    payload = body.get('payload') if isinstance(body, dict) else body
    if not payload:
        raise HTTPException(status_code=400, detail='No payload')
    if isinstance(payload, list):
        manager.tactical_targets.extend(payload)
    else:
        manager.tactical_targets.append(payload)
    manager.save_targets()
    # broadcast update
    await manager.broadcast(json.dumps({ 'type': 'update_targets', 'payload': manager.tactical_targets }))
    return JSONResponse({ 'status': 'ok', 'count': len(manager.tactical_targets) })


@app.delete('/api/targets')
async def api_delete_targets(authorization: str | None = Header(default=None)):
    if not validate_token(authorization):
        raise HTTPException(status_code=401, detail='Unauthorized')
    manager.tactical_targets = []
    manager.save_targets()
    await manager.broadcast(json.dumps({ 'type': 'update_targets', 'payload': manager.tactical_targets }))
    return JSONResponse({ 'status': 'cleared' })

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