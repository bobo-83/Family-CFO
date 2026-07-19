"""Household memory + conversation summarization (M57, ADR 0016).

After each chat exchange the household's own runtime extracts durable facts
from the user's message into ``household_memories`` and refreshes the rolling
summary of turns older than the history window. Everything here is a
best-effort side effect: chat never waits on it and never fails because of it.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.engine import Engine

from family_cfo_ai_orchestrator import RuntimeMessage, RuntimeUnavailableError

from family_cfo_api import repository
from family_cfo_api.ai_runtime_selection import select_tool_runtime
from family_cfo_api.config import Settings

logger = logging.getLogger(__name__)

# Bounds keeping the extra calls cheap and the prompt injection compact.
MAX_MEMORIES_PER_MESSAGE = 8
MAX_INJECTED_MEMORIES = 50
SUMMARY_SOURCE_MESSAGE_MAX_CHARS = 500
SUMMARY_WINDOW_MESSAGES = 8  # matches chat's _HISTORY_MAX_MESSAGES

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")

_EXTRACTION_SYSTEM_PROMPT = (
    "You extract durable household facts from a message a family member sent "
    "to their financial advisor. Return ONLY a JSON array (no prose, no code "
    "fences). Each element is {\"key\": snake_case identifier, \"value\": one "
    "short self-contained sentence}. Keys must be stable so a restated fact "
    "overwrites the old one (e.g. home_city, kids_count, "
    "eating_out_frequency, employer, rent_or_own). Extract only long-lived "
    "personal or household facts: where they live, family members, jobs, "
    "habits, preferences, recurring commitments. Do NOT extract questions, "
    "one-off purchase amounts, account balances, or anything the finance "
    "database already tracks. Return [] when there is nothing durable."
)

_SUMMARY_SYSTEM_PROMPT = (
    "Summarize the earlier part of a family's conversation with their "
    "financial advisor in at most 150 words of plain prose. Keep every "
    "concrete fact and figure (amounts, dates, decisions); drop pleasantries."
)


def parse_extracted_memories(text: str) -> list[tuple[str, str]]:
    """Parse the extractor's reply into (key, value) pairs, defensively.

    Model output is untrusted: code fences are stripped, non-JSON or wrongly
    shaped replies yield [], keys are validated and values bounded.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        value = item.get("value")
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        key = key.strip().lower()
        value = value.strip()
        if not _KEY_PATTERN.match(key) or not value:
            continue
        pairs.append((key, value[: repository.MEMORY_VALUE_MAX_LENGTH]))
        if len(pairs) >= MAX_MEMORIES_PER_MESSAGE:
            break
    return pairs


def extract_and_store_memories(
    runtime,
    engine: Engine,
    household_id: str,
    user_message: str,
    *,
    source_conversation_id: str | None = None,
) -> int:
    """Ask the runtime for durable facts in ``user_message`` and upsert them."""
    completion = runtime.complete(
        [
            RuntimeMessage(role="system", content=_EXTRACTION_SYSTEM_PROMPT),
            RuntimeMessage(role="user", content=user_message),
        ],
        temperature=0.0,
        max_tokens=400,
    )
    pairs = parse_extracted_memories(completion.text)
    for key, value in pairs:
        repository.upsert_household_memory(
            engine,
            household_id,
            key,
            value,
            source="chat",
            source_conversation_id=source_conversation_id,
        )
    return len(pairs)


def refresh_conversation_summary(runtime, engine: Engine, conversation_id: str) -> bool:
    """Re-summarize turns older than the history window; True when written."""
    messages = repository.list_conversation_messages(engine, conversation_id)
    if len(messages) <= SUMMARY_WINDOW_MESSAGES:
        return False
    older = messages[:-SUMMARY_WINDOW_MESSAGES]
    transcript = "\n".join(
        f"{m.role}: {m.content[:SUMMARY_SOURCE_MESSAGE_MAX_CHARS]}" for m in older
    )
    completion = runtime.complete(
        [
            RuntimeMessage(role="system", content=_SUMMARY_SYSTEM_PROMPT),
            RuntimeMessage(role="user", content=transcript),
        ],
        temperature=0.0,
        max_tokens=300,
    )
    summary = completion.text.strip()
    if not summary:
        return False
    repository.set_conversation_summary(engine, conversation_id, summary)
    return True


def remember_exchange(
    engine: Engine,
    household_id: str,
    conversation_id: str,
    user_message: str,
    settings: Settings | None = None,
) -> None:
    """Post-response background task: extract facts + refresh the summary.

    Never raises — chat already answered; a failure here only costs memory.
    """
    runtime = select_tool_runtime(engine, household_id, settings)
    if runtime is None:
        return
    try:
        extract_and_store_memories(
            runtime,
            engine,
            household_id,
            user_message,
            source_conversation_id=conversation_id,
        )
        refresh_conversation_summary(runtime, engine, conversation_id)
    except RuntimeUnavailableError:
        logger.warning("memory extraction skipped: runtime unavailable")
    except Exception:  # noqa: BLE001 — side effect must never break anything
        logger.exception("memory extraction failed")
    finally:
        runtime.close()


def run_memory_backfill_once(engine: Engine, settings: Settings | None = None) -> int:
    """One-time extraction over every household's surviving conversations.

    Runs at worker startup. Households already marked done are skipped; a
    household without a usable runtime is left unmarked so the next start
    retries. Returns the number of households backfilled this run.
    """
    done = 0
    for household_id in repository.list_households(engine):
        if repository.memory_backfill_done(engine, household_id):
            continue
        runtime = select_tool_runtime(engine, household_id, settings)
        if runtime is None:
            continue
        try:
            for conversation_id in repository.list_all_conversation_ids(engine, household_id):
                user_text = "\n".join(
                    m.content[:SUMMARY_SOURCE_MESSAGE_MAX_CHARS]
                    for m in repository.list_conversation_messages(engine, conversation_id)
                    if m.role == "user"
                )
                if not user_text:
                    continue
                extract_and_store_memories(
                    runtime,
                    engine,
                    household_id,
                    user_text,
                    source_conversation_id=conversation_id,
                )
            repository.mark_memory_backfill_done(engine, household_id)
            done += 1
        except RuntimeUnavailableError:
            logger.warning(
                "memory backfill deferred for household %s: runtime unavailable", household_id
            )
        except Exception:  # noqa: BLE001 — startup job must never kill the worker
            logger.exception("memory backfill failed for household %s", household_id)
        finally:
            runtime.close()
    return done
