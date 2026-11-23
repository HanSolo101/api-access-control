import uvicorn
from fastapi import FastAPI
from .router import router
from .admin import admin_router
from .usage import usage_router

app = FastAPI(
    title="Quotes API Access Control Service",
    description="Access control and rate-limiting service as specified in DESIGN.md",
    version="1.0.0",
)

app.include_router(router)
app.include_router(admin_router)
app.include_router(usage_router)

if __name__ == "__main__":
    uvicorn.run("src.app.main:app", host="0.0.0.0", port=8000, reload=True)
