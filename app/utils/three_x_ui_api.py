import json
from urllib.parse import quote
from datetime import datetime
import httpx

from app.setup_logger import logger

# Константа для перевода гигабайт в байты
GB_BYTES = 1073741824


class ThreeXUIServer:
    def __init__(self, id, url, indoub_id, login, password, need_gb=False, name='') -> None:
        self.id = id
        # Гарантируем, что URL заканчивается на слеш
        self.url = url.rstrip('/')
        self.indoub_id = int(indoub_id)
        self.login = login
        self.password = password
        self.need_gb = need_gb
        self.cookies = None
        self.name = name or f"Server-{id}"

    def _dict_to_string(self, obj: dict) -> str:
        """Внутренний метод для упаковки настроек в строку (требование 3x-ui)"""

        def default_encoder(o):
            if isinstance(o, datetime):
                return int(o.timestamp() * 1000)
            return str(o)

        return json.dumps(obj, ensure_ascii=False, default=default_encoder)

    async def _make_request(self, method: str, endpoint: str, payload: dict = None) -> dict | None:
        """
        Универсальный метод для всех запросов.
        Ловит таймауты, ошибки сети и автоматически авторизуется при необходимости.
        """
        clean_endpoint = endpoint.lstrip('/')

        # Если нет куки и это не запрос логина — сначала авторизуемся
        if not self.cookies and clean_endpoint != "login":
            if not await self.auth():
                return None  # Прерываем запрос, если авторизация не удалась

        url = f"{self.url}/{clean_endpoint}"

        # Маскируемся под обычный браузер Chrome (спасает от блокировок User-Agent)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }

        try:
            # УВЕЛИЧЕН таймаут до 15.0 секунд
            async with httpx.AsyncClient(verify=False, timeout=5, headers=headers) as client:
                if method.upper() == "GET":
                    response = await client.get(url, cookies=self.cookies)
                else:
                    response = await client.post(url, json=payload, cookies=self.cookies)

                # Обработка успешного запроса логина (сохраняем куки)
                if clean_endpoint == "login" and response.status_code == 200:
                    self.cookies = response.cookies

                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"Ошибка HTTP {response.status_code} от сервера {self.name} при запросе {url}")
                    return None

        except httpx.RequestError as e:
            # Добавили type(e).__name__, чтобы точно видеть тип ошибки (Timeout, ConnectError и т.д.)
            logger.error(f"Сетевая ошибка/Таймаут с сервером {self.name} ({url}): {type(e).__name__} {e}")
            return None
        except Exception as e:
            logger.error(f"Неизвестная ошибка с сервером {self.name} ({url}): {e}")
            return None

    async def auth(self) -> bool:
        """Авторизация в панели"""
        data = {
            'username': self.login,
            'password': self.password,
            'twoFactorCode': ''
        }

        response_data = await self._make_request("POST", "login", data)

        if response_data and response_data.get('success'):
            return True

        msg = response_data.get('msg') if response_data else 'Нет ответа'
        logger.warning(f"Не удалось авторизоваться на {self.name}: {msg}")
        self.cookies = None
        return False

    async def add_client(
            self, uuid: str, email: str, limit_ip: int, expiry_time: int,
            tg_id: str, name: str, total_gb: int = 0, flow: str = "xtls-rprx-vision"
    ) -> bool:
        traffic_limit = (total_gb if total_gb else 30) * GB_BYTES if self.need_gb else 0

        data = {
            "id": self.indoub_id,
            "settings": self._dict_to_string({
                "clients": [{
                    "id": uuid, "alterId": 0, "email": email, "limitIp": limit_ip,
                    "expiryTime": expiry_time, "enable": True, "comment": name,
                    "tgId": str(tg_id), "subId": uuid.split('-')[-1],
                    "totalGB": traffic_limit, "flow": flow
                }]
            })
        }

        response = await self._make_request("POST", "panel/api/inbounds/addClient", data)
        if response and response.get('success'):
            logger.info(f"Добавлен клиент {name} на {self.name}")
            return True

        logger.warning(
            f"Ошибка добавления клиента {name} на {self.name}: {response.get('msg') if response else 'Сбой сети'}")
        return False

    async def edit_client(
            self, uuid: str, name: str, email: str, limit_ip: int, expiry_time: int,
            tg_id: str, total_gb: int = 0, flow: str = "xtls-rprx-vision"
    ) -> bool:
        traffic_limit = (total_gb if total_gb else 30) * GB_BYTES if self.need_gb else 0

        data = {
            "id": self.indoub_id,
            "settings": self._dict_to_string({
                "clients": [{
                    "id": uuid, "alterId": 0, "email": email, "limitIp": limit_ip,
                    "expiryTime": expiry_time, "enable": True, "comment": name,
                    "tgId": str(tg_id), "subId": uuid.split('-')[-1],
                    "totalGB": traffic_limit, "flow": flow
                }]
            })
        }

        response = await self._make_request("POST", f"panel/api/inbounds/updateClient/{uuid}", data)
        if response and response.get('success'):
            logger.info(f"Изменен клиент {email} на {self.name}")
            return True

        logger.warning(
            f"Ошибка изменения клиента {email} на {self.name}: {response.get('msg') if response else 'Сбой сети'}")
        return False

    async def client_remain_trafic(self, uuid: str) -> tuple | bool:
        """Возвращает кортеж (up, down, total) в байтах или False"""
        response = await self._make_request("GET", f"panel/api/inbounds/getClientTrafficsById/{uuid}")

        up, down, total = 0, 0, 0

        # 1. Пытаемся получить статистику использования (up/down)
        if response and response.get('success'):
            obj_list = response.get('obj')
            if obj_list and len(obj_list) > 0:
                obj = obj_list[0]
                up = obj.get('up', 0)
                down = obj.get('down', 0)
                total = obj.get('total', 0)

        if total == 0 and self.need_gb:
            client_data = await self.get_client_by_uuid(uuid)
            if client_data:
                # Берем лимит прямо из конфига клиента
                total = client_data.get('totalGB', 0)

            # Резервный вариант: если даже в конфиге пусто, отдаем жесткие 30 ГБ
            if total == 0:
                total = 30 * GB_BYTES

        return (up, down, total)
    async def get_total_gb(self, uuid: str) -> int:
        """Текущий лимит totalGB в ГБ"""
        traf = await self.client_remain_trafic(uuid)
        if traf is False:
            return 0
        total_bytes = traf[2] or 0
        return int(total_bytes // GB_BYTES)

    async def get_client_vless(self, uuid: str) -> str | None:
        """Генерирует ссылку VLESS для клиента"""
        response = await self._make_request("GET", f"panel/api/inbounds/get/{self.indoub_id}")

        if not response or not response.get('success'):
            return None

        data = response['obj']
        settings = json.loads(data['settings'])
        stream_settings = json.loads(data['streamSettings'])

        # Надежное извлечение IP адреса сервера
        ip = self.url.split('/')[2].replace('https://', '').replace('http://', '').split(':')[0]

        client_obj = next((i for i in settings.get('clients', []) if i.get('id') == uuid), None)

        if not client_obj:
            logger.warning(f"Клиент {uuid} не найден на сервере {self.name}")
            return None

        # Формирование VLESS ссылки
        try:
            rs = stream_settings.get('realitySettings', {})
            rs_settings = rs.get('settings', {})
            path = stream_settings.get('xhttpSettings', {}).get('path', '') or stream_settings.get('wsSettings',
                                                                                                   {}).get('path', '')
            target_sni = rs.get('target', 'none').split(':')[0]
            short_id = rs.get('shortIds', [''])[0] if rs.get('shortIds') else ''

            client_name_url = quote(self.name if self.name else client_obj['email'].split('_')[0])

            vless_url = (
                f"vless://{uuid}@{ip}:{data['port']}?"
                f"type={stream_settings.get('network', 'tcp')}&"
                f"security={stream_settings.get('security', 'none')}&"
                f"encryption={settings.get('encryption', 'none')}&"
                f"path={path}&"
                f"pbk={rs_settings.get('publicKey', 'none')}&"
                f"fp={rs_settings.get('fingerprint', 'none')}&"
                f"sni={target_sni}&"
                f"sid={short_id}&"
                f"spx=%2F&flow={client_obj.get('flow', '')}#{client_name_url}"
            )
            return vless_url
        except Exception as e:
            logger.error(f"Ошибка парсинга VLESS ссылки на {self.name}: {e}")
            return None

    async def delete_client(self, uuid: str) -> bool:
        response = await self._make_request("POST", f"panel/api/inbounds/{self.indoub_id}/delClient/{uuid}")

        if response and response.get('success'):
            logger.info(f"Удален клиент {uuid} с {self.name}")
            return True

        logger.warning(f"Не удалось удалить клиента {uuid} на {self.name}")
        return False

    async def reset_client_traffic(self, email: str) -> bool:
        response = await self._make_request("POST", f"panel/api/inbounds/{self.indoub_id}/resetClientTraffic/{email}")

        if response and response.get('success'):
            logger.info(f"Сброшен трафик клиента {email} на {self.name}")
            return True

        logger.warning(f"Не удалось сбросить трафик {email} на {self.name}")
        return False

    async def get_client_by_uuid(self, uuid: str) -> dict | None:
        """Достаёт объект клиента из inbound settings по uuid."""
        response = await self._make_request("GET", f"panel/api/inbounds/get/{self.indoub_id}")

        if not response or not response.get("success"):
            return None

        inbound = response.get("obj") or {}
        settings_raw = inbound.get("settings")
        if not settings_raw:
            return None

        settings = json.loads(settings_raw)
        return next((c for c in settings.get("clients", []) if c.get("id") == uuid), None)