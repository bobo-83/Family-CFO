from fastapi import APIRouter

from family_cfo_api.api.accounts import router as accounts_router
from family_cfo_api.api.advisor import router as advisor_router
from family_cfo_api.api.ai_runtime import router as ai_runtime_router
from family_cfo_api.api.audit_log import router as audit_router
from family_cfo_api.api.auth import router as auth_router
from family_cfo_api.api.backups import router as backups_router
from family_cfo_api.api.bills import router as bills_router
from family_cfo_api.api.budgets import router as budgets_router
from family_cfo_api.api.categories import router as categories_router
from family_cfo_api.api.chat import router as chat_router
from family_cfo_api.api.connections import router as connections_router
from family_cfo_api.api.conversations import router as conversations_router
from family_cfo_api.api.documents import router as documents_router
from family_cfo_api.api.goals import router as goals_router
from family_cfo_api.api.health import router as health_router
from family_cfo_api.api.household import router as household_router
from family_cfo_api.api.households import router as households_router
from family_cfo_api.api.imports import router as imports_router
from family_cfo_api.api.income import router as income_router
from family_cfo_api.api.income_analysis import router as income_analysis_router
from family_cfo_api.api.members import router as members_router
from family_cfo_api.api.memories import router as memories_router
from family_cfo_api.api.pairing import router as pairing_router
from family_cfo_api.api.reports import router as reports_router
from family_cfo_api.api.transactions import router as transactions_router
from family_cfo_api.api.voice import router as voice_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(households_router)
api_router.include_router(pairing_router)
api_router.include_router(household_router)
api_router.include_router(members_router)
api_router.include_router(accounts_router)
api_router.include_router(transactions_router)
api_router.include_router(categories_router)
api_router.include_router(budgets_router)
api_router.include_router(bills_router)
api_router.include_router(income_router)
api_router.include_router(income_analysis_router)
api_router.include_router(goals_router)
api_router.include_router(advisor_router)
api_router.include_router(ai_runtime_router)
api_router.include_router(chat_router)
api_router.include_router(voice_router)
api_router.include_router(memories_router)
api_router.include_router(connections_router)
api_router.include_router(conversations_router)
api_router.include_router(imports_router)
api_router.include_router(documents_router)
api_router.include_router(reports_router)
api_router.include_router(backups_router)
api_router.include_router(audit_router)
