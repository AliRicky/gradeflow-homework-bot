from fastapi import FastAPI, Request
from telegram import Update
from bot import create_app
from tma_admin import create_admin_router

app = FastAPI(title="TA Homework Collector Bot API")
tg_app = None
_initialized = False

async def init_tg_app():
    global tg_app, _initialized
    if not _initialized:
        tg_app = create_app()
        await tg_app.initialize()
        _initialized = True
    return tg_app

def get_tg_bot():
    global tg_app
    if tg_app and hasattr(tg_app, "bot") and tg_app.bot:
        return tg_app.bot
    import bot as bot_module
    try:
        from telegram import Bot
        if bot_module.BOT_TOKEN:
            return Bot(token=bot_module.BOT_TOKEN)
    except Exception:
        pass
    return None

# Mount TMA Admin Router
admin_router = create_admin_router(get_bot_func=lambda: get_tg_bot())
app.include_router(admin_router)

@app.post("/")
async def webhook(request: Request):
    await init_tg_app()
    update_data = await request.json()
    update = Update.de_json(update_data, tg_app.bot)
    await tg_app.process_update(update)
    return {"status": "ok"}

@app.get("/")
async def index():
    return {"status": "Bot is operational"}

@app.post("/api")
async def webhook_api(request: Request):
    return await webhook(request)

@app.get("/api")
async def index_api():
    return {"status": "Bot is operational"}
