from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os

# Ініціалізація FastAPI
app = FastAPI(title="Tactical Analytics API", version="1.0")

# Налаштування CORS (щоб фронтенд міг робити запити до бекенду)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Конфігурація Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
SERVICE_ACCOUNT_FILE = 'credentials.json' 
TARGET_FOLDER_ID = '1gFTEOPJRFErU-0cnUx1kmt6GC-hxirKZ' 

def get_drive_service():
    """Аутентифікація та підключення до Google Drive API"""
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"Файл ключів {SERVICE_ACCOUNT_FILE} не знайдено.")
    
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

@app.get("/api/v1/analytics/reports", tags=["Intelligence"])
async def get_pdf_reports():
    """Ендпоінт для отримання списку PDF-звітів з Google Диску"""
    try:
        service = get_drive_service()
        
        # Шукаємо тільки PDF файли у визначеній папці
        query = f"'{TARGET_FOLDER_ID}' in parents and mimeType='application/pdf' and trashed=false"
        
        results = service.files().list(
            q=query,
            pageSize=50,
            fields="files(id, name, createdTime, webViewLink, webContentLink)",
            orderBy="createdTime desc"
        ).execute()
        
        items = results.get('files', [])
        
        # Форматуємо відповідь для дашборду
        formatted_reports = []
        for item in items:
            formatted_reports.append({
                "id": item.get('id'),
                "name": item.get('name'),
                "date": item.get('createdTime').split('T')[0],
                "view_url": item.get('webViewLink'), # Лінк для перегляду в браузері
                "download_url": item.get('webContentLink') # Лінк для прямого завантаження
            })
            
        return {"status": "success", "count": len(formatted_reports), "data": formatted_reports}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Запуск сервера на порту 8000
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
