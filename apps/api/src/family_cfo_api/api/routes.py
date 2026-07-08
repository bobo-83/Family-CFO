from fastapi import APIRouter

from family_cfo_api.api.accounts import router as accounts_router
from family_cfo_api.api.advisor import router as advisor_router
from family_cfo_api.api.ai_runtime import router as ai_runtime_router
from family_cfo_api.api.auth import router as auth_router
from family_cfo_api.api.bills import router as bills_router
from family_cfo_api.api.chat import router as chat_router
from family_cfo_api.api.goals import router as goals_router
from family_cfo_api.api.health import router as health_router
from family_cfo_api.api.household import router as household_router
from family_cfo_api.api.income import router as income_router
from family_cfo_api.api.pairing import router as pairing_router
from family_cfo_api.api.transactions import router as transactions_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(pairing_router)
api_router.include_router(household_router)
api_router.include_router(accounts_router)
api_router.include_router(transactions_router)
api_router.include_router(bills_router)
api_router.include_router(income_router)
api_router.include_router(goals_router)
api_router.include_router(advisor_router)
api_router.include_router(ai_runtime_router)
api_router.include_router(chat_router)
