from pydantic import BaseModel


class UpdateClientGS(BaseModel):
    user_id: int
    devices: int
    sub_time: str


