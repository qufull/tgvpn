import asyncio
import json
import time
from urllib.parse import quote
from datetime import datetime
import httpx
from app import http_client

from app.setup_logger import logger

GB_BYTES = 1073741824


class ThreeXUIServer:
    # 🌐 ГЛОБАЛЬНАЯ ПАМЯТЬ ДЛЯ ВСЕХ ПОТОКОВ
    _offline_until = {}
    _auth_locks = {}
    _write_locks = {}  # 🛡 НОВОЕ: Глобальная очередь для записи (защита от database is locked)

    def __init__(self, id, url, indoub_id, login, password, need_gb=False, name='') -> None:
        self.id = id
        self.url = url.rstrip('/')
        self.indoub_id = int(indoub_id)
        self.login = login
        self.password = password
        self.need_gb = need_gb
        self.cookies = None
        self.name = name or f"Server-{id}"
        self._cooldown_seconds = 300

        # Индивидуальные замки для каждого сервера
        if self.id not in ThreeXUIServer._auth_locks:
            ThreeXUIServer._auth_locks[self.id] = asyncio.Lock()
        if self.id not in ThreeXUIServer._write_locks:
            ThreeXUIServer._write_locks[self.id] = asyncio.Lock()

        self._auth_lock = ThreeXUIServer._auth_locks[self.id]
        self._write_lock = ThreeXUIServer._write_locks[self.id]  # 🛡 Замок для записи

    # --- МЕТОДЫ ПРЕДОХРАНИТЕЛЯ ---
    @property
    def is_offline(self):
        """Проверяет, находится ли сервер на глобальной паузе"""
        return time.time() < ThreeXUIServer._offline_until.get(self.id, 0)

    def set_offline(self):
        """Отправляет сервер в нокаут на 5 минут"""
        if not self.is_offline:
            ThreeXUIServer._offline_until[self.id] = time.time() + self._cooldown_seconds
            logger.error(f"🚨 Сервер {self.name} не отвечает. Ставим на ГЛОБАЛЬНУЮ паузу на {self._cooldown_seconds // 60} мин.")

    def clear_offline(self):
        """Оживляет сервер после успешного запроса"""
        if self.id in ThreeXUIServer._offline_until:
            del ThreeXUIServer._offline_until[self.id]

    def _dict_to_string(self, obj: dict) -> str:
        def default_encoder(o):
            if isinstance(o, datetime):
                return int(o.timestamp() * 1000)
            return str(o)
        return json.dumps(obj, ensure_ascii=False, default=default_encoder)

    async def _make_request(self, method: str, endpoint: str, payload: dict = None, retries: int = 2) -> dict | None:
        if self.is_offline:
            return None  # ⚡️ Моментальный отказ, если сервер мертв

        clean_endpoint = endpoint.lstrip('/')

        if not self.cookies and clean_endpoint != "login":
            if not await self.auth():
                return None

        url = f"{self.url}/{clean_endpoint}"

        if http_client.client is None:
            logger.error("HTTP клиент не инициализирован!")
            return None

        for attempt in range(1, retries + 1):
            try:
                # 🚀 ИСПОЛЬЗУЕМ ГЛОБАЛЬНЫЙ КЛИЕНТ БЕЗ КОНТЕКСТНОГО МЕНЕДЖЕРА (БЕЗ async with)
                if method.upper() == "GET":
                    response = await http_client.client.get(url, cookies=self.cookies)
                else:
                    response = await http_client.client.post(url, json=payload, cookies=self.cookies)

                # ... дальше идет ваша стандартная логика обработки ответа (response.status_code == 200 и т.д.) ...

                if clean_endpoint == "login" and response.status_code == 200:
                    self.cookies = response.cookies
                    self.clear_offline()
                    return response.json()

                if response.status_code == 200:
                    data = response.json()
                    if not data.get('success') and 'login' in str(data.get('msg', '')).lower():
                        self.cookies = None
                        if await self.auth(): continue
                        return None
                    self.clear_offline()
                    return data

                else:
                    if attempt < retries:
                        logger.warning(f"Ошибка {response.status_code} от {self.name}. Сбрасываем и перелогиниваемся.")
                        self.cookies = None
                        if await self.auth():
                            continue

                    logger.warning(f"Ошибка HTTP {response.status_code} от сервера {self.name} при запросе {url}")
                    return None

            except asyncio.CancelledError:
                logger.error(f"🚨 Запрос к {self.name} был принудительно прерван (слишком долго).")
                self.set_offline()
                raise
            except httpx.TimeoutException:
                if attempt == retries:
                    self.set_offline()
                    return None
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Сетевая ошибка с {self.name}: {e}")
                self.set_offline()
                return None

    async def auth(self) -> bool:
        """Авторизация с проверкой глобальной паузы"""
        if self.is_offline:
            return False

        async with self._auth_lock:
            if self.cookies is not None:
                return True

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

            self.set_offline()

            return False

    async def add_client(self, uuid: str, email: str, limit_ip: int, expiry_time: int, tg_id: str, name: str,
                         total_gb: int = 0, flow: str = "xtls-rprx-vision") -> bool:
        # Встаем в очередь, чтобы не повесить базу данных SQLite в 3x-ui
        async with self._write_lock:
            traffic_limit = (total_gb if total_gb else 30) * GB_BYTES if self.need_gb else 0
            data = {"id": self.indoub_id, "settings": self._dict_to_string({"clients": [
                {"id": uuid, "alterId": 0, "email": email, "limitIp": limit_ip, "expiryTime": expiry_time,
                 "enable": True, "comment": name, "tgId": str(tg_id), "subId": uuid.split('-')[-1],
                 "totalGB": traffic_limit, "flow": flow}]})}
            response = await self._make_request("POST", "panel/api/inbounds/addClient", data)
            if response and response.get('success'):
                logger.info(f"✅ Добавлен клиент {name} на {self.name}")
                return True
            return False

    async def edit_client(self, uuid: str, name: str, email: str, limit_ip: int, expiry_time: int, tg_id: str, total_gb: int = 0, flow: str = "xtls-rprx-vision") -> bool:
        async with self._write_lock:
            traffic_limit = (total_gb if total_gb else 30) * GB_BYTES if self.need_gb else 0
            data = {"id": self.indoub_id, "settings": self._dict_to_string({"clients": [{"id": uuid, "alterId": 0, "email": email, "limitIp": limit_ip, "expiryTime": expiry_time, "enable": True, "comment": name, "tgId": str(tg_id), "subId": uuid.split('-')[-1], "totalGB": traffic_limit, "flow": flow}]})}
            response = await self._make_request("POST", f"panel/api/inbounds/updateClient/{uuid}", data)
            return bool(response and response.get('success'))

    async def client_remain_trafic(self, uuid: str) -> tuple | bool:
        response = await self._make_request("GET", f"panel/api/inbounds/getClientTrafficsById/{uuid}")
        up, down, total = 0, 0, 0
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
                total = client_data.get('totalGB', 0)
            if total == 0:
                total = 30 * GB_BYTES
        return (up, down, total)

    async def get_total_gb(self, uuid: str) -> int:
        traf = await self.client_remain_trafic(uuid)
        if traf is False:
            return 0
        total_bytes = traf[2] or 0
        return int(total_bytes // GB_BYTES)

    async def get_client_vless(self, uuid: str) -> str | None:
        response = await self._make_request("GET", f"panel/api/inbounds/get/{self.indoub_id}")
        if not response or not response.get('success'):
            return None

        data = response['obj']
        settings = json.loads(data['settings'])
        stream_settings = json.loads(data['streamSettings'])
        ip = self.url.split('/')[2].replace('https://', '').replace('http://', '').split(':')[0]
        client_obj = next((i for i in settings.get('clients', []) if i.get('id') == uuid), None)

        if not client_obj:
            return None

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
        async with self._write_lock:
            response = await self._make_request("POST", f"panel/api/inbounds/{self.indoub_id}/delClient/{uuid}")
            return bool(response and response.get('success'))

    async def reset_client_traffic(self, email: str) -> bool:
        response = await self._make_request("POST", f"panel/api/inbounds/{self.indoub_id}/resetClientTraffic/{email}")
        if response and response.get('success'):
            logger.info(f"Сброшен трафик клиента {email} на {self.name}")
            return True
        return False

    async def get_client_by_uuid(self, uuid: str) -> dict | None:
        response = await self._make_request("GET", f"panel/api/inbounds/get/{self.indoub_id}")
        if not response or not response.get("success"):
            return None
        inbound = response.get("obj") or {}
        settings_raw = inbound.get("settings")
        if not settings_raw:
            return None
        settings = json.loads(settings_raw)
        return next((c for c in settings.get("clients", []) if c.get("id") == uuid), None)