import os
from dotenv import load_dotenv

load_dotenv()

from app.bootstrap.init_app import bootstrap_from_env

app = bootstrap_from_env()

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "3239"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, access_log=False)
