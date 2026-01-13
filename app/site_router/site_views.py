from fastapi import APIRouter 
from starlette.responses import FileResponse


site_router = APIRouter(prefix="/site")


@site_router.get('/')
async def main_page():
    return FileResponse('app/site_router/templates/index.html')


@site_router.get('/privacy_policy')
async def private_policy_page():
    return FileResponse('app/site_router/templates/privacy-policy.html')


@site_router.get('/terms_of_service')
async def terms_of_service_page():
    return FileResponse('app/site_router/templates/terms-of-service.html')


