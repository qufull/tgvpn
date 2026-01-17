import json
from urllib.parse import quote

from httpx import AsyncClient

from app.setup_logger import logger


class ThreeXUIServer:
    def __init__(self, id, url, indoub_id, login, password, need_gb=False, name='') -> None:
        self.id = id
        self.url = url
        self.indoub_id = int(indoub_id)
        self.login = login
        self.password = password
        self.need_gb = need_gb
        self.cookies = None
        self.name = name

    def strin_to_dict(self, string):
        return json.loads(string)

    def dict_to_sting(self, obj):
        return json.dumps(obj, indent=4, ensure_ascii=False)

    async def auth(self):
        data = {
            'username': self.login,
            'password': self.password,
            'twoFactorCode': ''
        }
        async with AsyncClient() as client:
            response = await client.post(url=self.url + 'login', json=data)
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    pass
                else:
                    logger.warning(f"Не удалось авторизоваться: {self.url} - {data.get('msg')}")
            else:
                logger.warning(f"Не удалось авторизоваться: {self.url} - {response.status_code}")

            self.cookies = response.cookies

    async def add_client(
        self,
        uuid: str,
        email: str,
        limit_ip: int,
        expiry_time: int,
        tg_id: str,
        name: str,
        total_gb: int = 0
    ):
        if not self.cookies:
            await self.auth()

        if self.need_gb:
            traffic_limit = (total_gb if total_gb else 30) * 1073741824
        else:
            traffic_limit = 0

        data = {
            "id": self.indoub_id,
            "settings": self.dict_to_sting({
                "clients": [{
                    "id": uuid,
                    "alterId": 0,
                    "email": email,
                    "limitIp": limit_ip,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "comment": name,
                    "tgId": str(tg_id),
                    "subId": uuid.split('-')[-1],
                    "totalGB": traffic_limit
                }]
            })
        }

        async with AsyncClient() as client:
            response = await client.post(
                url=self.url + "panel/api/inbounds/addClient",
                json=data,
                cookies=self.cookies
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    logger.info(f"Добавлен клиент {name}")
                    return True
                logger.warning(f"Не удалось добавить клиента {name}: {data.get('msg')}")
                return False

            logger.warning(f"Не удалось добавить клиента {name}: {self.url} - {response.status_code}")
            return False

    async def edit_client(
        self,
        uuid: str,
        name: str,
        email: str,
        limit_ip: int,
        expiry_time: int,
        tg_id: str,
        total_gb: int = 0
    ):
        if not self.cookies:
            await self.auth()

        # total_gb передаём в ГБ (как в add_client). Здесь приводим к байтам только для need_gb панелей.
        if self.need_gb:
            traffic_limit = (total_gb if total_gb else 30) * 1073741824
        else:
            traffic_limit = 0

        data = {
            "id": self.indoub_id,
            "settings": self.dict_to_sting({
                "clients": [{
                    "id": uuid,
                    "alterId": 0,
                    "email": email,
                    "limitIp": limit_ip,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "tgId": str(tg_id),
                    "subId": uuid.split('-')[-1],
                    "comment": name,
                    "totalGB": traffic_limit
                }]
            })
        }

        async with AsyncClient() as client:
            response = await client.post(
                url=self.url + f"panel/api/inbounds/updateClient/{uuid}",
                json=data,
                cookies=self.cookies
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    logger.info(f"Изменен клиент {email}")
                    return True
                logger.warning(f"Не удалось изменить клиента {email}: {data.get('msg')}")
                return False

            logger.warning(f"Не удалось изменить клиента {email}: {response.status_code}")
            return False

    async def client_remain_trafic(self, uuid: str):
        if not self.cookies:
            await self.auth()

        async with AsyncClient() as client:
            response = await client.get(
                url=f"{self.url}panel/api/inbounds/getClientTrafficsById/{uuid}",
                cookies=self.cookies
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    # up/down/total — в байтах
                    return (data['obj'][0]['up'], data['obj'][0]['down'], data['obj'][0]['total'])
                logger.warning(f"Не удалось получить трафик клиента {uuid}: {data.get('msg')}")
                return False

            logger.warning(f"Не удалось получить трафик клиента {uuid}: {response.status_code}")
            return False

    async def get_total_gb(self, uuid: str) -> int:
        """Текущий лимит totalGB в ГБ (по данным getClientTrafficsById)."""
        traf = await self.client_remain_trafic(uuid)
        if not traf:
            return 0
        total_bytes = traf[2] or 0
        return int(total_bytes // 1073741824)

    async def get_client_vless(self, uuid: str):
        if not self.cookies:
            await self.auth()

        async with AsyncClient() as client:
            response = await client.get(
                url=f"{self.url}panel/api/inbounds/get/{self.indoub_id}",
                cookies=self.cookies
            )

            if response.status_code != 200:
                logger.warning(f"Не удалось подключиться к индаубу: {self.url} - {response.status_code}")
                return

            data = response.json()['obj']
            settings = self.strin_to_dict(data['settings'])
            stream_settings = self.strin_to_dict(data['streamSettings'])
            ip = self.url.split('/')[2].replace('https://', '').replace('http://', '').split(':')[0]

            client_obj = {}
            for i in settings['clients']:
                if i['id'] == uuid:
                    client_obj = i
                    break

            if not client_obj:
                logger.warning("Клиент не найден")
                return

            return (
                f"vless://{uuid}@{ip}:{data['port']}?"
                f"type={stream_settings['network']}&"
                f"security={stream_settings.get('security', 'none')}&"
                f"encryption={settings.get('encryption', 'none')}&"
                f"path={stream_settings.get('xhttpSettings', {}).get('path', '') or stream_settings.get('wsSettings', {}).get('path', '')}&"
                f"pbk={stream_settings.get('realitySettings', {}).get('settings', {}).get('publicKey', 'none')}&"
                f"fp={stream_settings.get('realitySettings', {}).get('settings', {}).get('fingerprint', 'none')}&"
                f"sni={stream_settings.get('realitySettings', {}).get('target', 'none').split(':')[0]}&"
                f"sid={stream_settings.get('realitySettings', {}).get('shortIds', [''])[0]}&"
                f"spx=%2F&flow={client_obj.get('flow', '')}#{quote(client_obj['email'].split('_')[0])}"
            )

    async def delete_client(self, uuid: str):
        if not self.cookies:
            await self.auth()

        async with AsyncClient() as client:
            response = await client.post(
                url=self.url + f"panel/api/inbounds/{self.indoub_id}/delClient/{uuid}",
                cookies=self.cookies
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    logger.info(f"Удален клиент {uuid}")
                    return True
                logger.warning(f"Не удалось удалить клиента {uuid}: {data.get('msg')}")
                return False

            logger.warning(f"Не удалось удалить клиента {uuid}: {response.status_code}")
            return False

    async def reset_client_traffic(self, email: str):
        """Сбросить трафик клиента по email"""
        if not self.cookies:
            await self.auth()

        async with AsyncClient() as client:
            response = await client.post(
                url=f"{self.url}panel/api/inbounds/{self.indoub_id}/resetClientTraffic/{email}",
                cookies=self.cookies
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    logger.info(f"Сброшен трафик клиента {email}")
                    return True
                logger.warning(f"Не удалось сбросить трафик {email}: {data.get('msg')}")
                return False

            logger.warning(f"Не удалось сбросить трафик {email}: {response.status_code}")
            return False


    async def get_client_by_uuid(self, uuid: str) -> dict | None:
        """Достаёт объект клиента из inbound settings по uuid."""
        if not self.cookies:
            await self.auth()

        async with AsyncClient() as client:
            response = await client.get(
                url=f"{self.url}panel/api/inbounds/get/{self.indoub_id}",
                cookies=self.cookies
            )

            if response.status_code != 200:
                logger.warning(f"Не удалось получить inbound {self.indoub_id}: {response.status_code}")
                return None

            data = response.json()
            if not data.get("success"):
                logger.warning(f"Не удалось получить inbound {self.indoub_id}: {data.get('msg')}")
                return None

            inbound = data.get("obj") or {}
            settings_raw = inbound.get("settings")
            if not settings_raw:
                return None

            settings = self.strin_to_dict(settings_raw)
            for c in settings.get("clients", []):
                if c.get("id") == uuid:
                    return c

        return None
