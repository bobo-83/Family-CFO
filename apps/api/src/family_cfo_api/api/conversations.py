import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    Conversation,
    ConversationDetail,
    ConversationListResponse,
    ConversationMessage,
    ErrorResponse,
)

router = APIRouter(tags=["Conversations"])
logger = logging.getLogger(__name__)


def _to_summary(record: repository.ConversationRecord) -> Conversation:
    return Conversation(
        id=record.id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
        message_count=record.message_count,
    )


@router.get(
    "/conversations",
    operation_id="listConversations",
    response_model=ConversationListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List chat conversations for the household",
)
async def list_conversations(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> ConversationListResponse:
    records = repository.list_conversations(engine, session.household_id)
    return ConversationListResponse(conversations=[_to_summary(record) for record in records])


@router.get(
    "/conversations/{conversation_id}",
    operation_id="getConversation",
    response_model=ConversationDetail,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Conversation not found", "model": ErrorResponse},
    },
    summary="Get a conversation and its message thread",
)
async def get_conversation(
    conversation_id: str,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> ConversationDetail:
    record = repository.get_conversation(engine, session.household_id, conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = repository.list_conversation_messages(engine, conversation_id)
    return ConversationDetail(
        id=record.id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
        messages=[
            ConversationMessage(
                id=message.id,
                role=message.role,
                content=message.content,
                recommendation_id=message.recommendation_id,
                sequence=message.sequence,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.delete(
    "/conversations/{conversation_id}",
    operation_id="deleteConversation",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Conversation not found", "model": ErrorResponse},
    },
    summary="Delete a conversation and its messages",
)
async def delete_conversation(
    conversation_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    if not repository.delete_conversation(engine, session.household_id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    logger.info(
        "conversation deleted household_id=%s conversation_id=%s",
        session.household_id,
        conversation_id,
    )
    return Response(status_code=204)
