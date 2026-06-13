from server import app, get_lan_url


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"War Room Console: http://localhost:{port}")
    print(f"Android/iPhone URL on this Wi-Fi network: {get_lan_url(port)}")
    uvicorn.run(app, host="0.0.0.0", port=port)
