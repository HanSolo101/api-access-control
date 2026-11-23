import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "admin")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "secretpassword")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "api_access_control")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD")
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "super-secret-admin-token")
    PEPPER: str = os.getenv("PEPPER", "default-pepper")

settings = Settings()
