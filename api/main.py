from fastapi import FastAPI

from api.routes import auth, health, sync, webhooks

app = FastAPI(
    title="Marketing & Sales Data Hub - API",
    description="Recebe OAuth callback e webhooks do RD Station CRM, e dispara sincronizacoes.",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(webhooks.router)
app.include_router(sync.router)


@app.get("/")
def root():
    return {
        "service": "dashboard-com-api",
        "docs": "/docs",
        "auth_login": "/auth/rd/login",
        "webhook_endpoint": "/webhooks/rd",
    }
