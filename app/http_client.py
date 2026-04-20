import httpx
from app.setup_logger import logger

# Глобальная переменная
client: httpx.AsyncClient | None = None


async def init_http_client():
    global client
    # Настраиваем лимиты:
    # max_keepalive_connections - сколько соединений держим открытыми
    # max_connections - жесткий лимит одновременных коннектов
    limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)

    client = httpx.AsyncClient(
        verify=False,
        timeout=12.0,
        limits=limits,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }
    )
    logger.info("Глобальный HTTP-клиент успешно запущен")


async def close_http_client():
    global client
    if client:
        await client.aclose()
        client = None
        logger.info("Глобальный HTTP-клиент закрыт")