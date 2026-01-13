from typing import Union

from fastapi import Form
from pydantic import BaseModel


class PayResponce(BaseModel):
    OutSum: Union[str, float, int] = Form(...)
    InvId: Union[str, float, int] = Form(...)
    Fee: Union[str, float, int, None] = Form(None)
    SignatureValue: str = Form(...)
    EMail: Union[str, None] = Form(None)
    PaymentMethod: Union[str, None] = Form(None)
    IncCurrLabel: Union[str, None] = Form(None)
    Shp_Receipt: Union[str, None] = Form(None)
