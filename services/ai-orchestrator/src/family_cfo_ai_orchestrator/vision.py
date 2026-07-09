"""Image description for chat photo attachments (ADR 0011: describe-then-ground).

The image is always turned into text by a single no-tools completion — either
on the vision-capable main model or on a dedicated small describer — and only
the resulting description enters the chat pipeline. Its numbers are then
grounded by the caller, since they trace to a real source (the photo).
"""

from __future__ import annotations

from family_cfo_ai_orchestrator.runtime import RuntimeAdapter, RuntimeMessage

DESCRIBE_PROMPT_VERSION = "v1"

_DESCRIBE_SYSTEM = (
    "You describe photos for a household financial assistant. Describe what the "
    "image shows, transcribing exactly any financially relevant details you can "
    "see: item names, prices, totals, dates, account numbers partially if shown, "
    "merchant names, and quantities. Only state what is actually visible; if a "
    "detail is unreadable, say so rather than guessing. Keep it under 150 words."
)


def describe_image(
    runtime: RuntimeAdapter,
    image_data_url: str,
    *,
    user_context: str = "",
    max_tokens: int = 300,
) -> str:
    """One multimodal completion turning a photo into grounded-able text.

    Raises RuntimeUnavailableError (from the adapter) if the runtime is down;
    the caller decides how to degrade.
    """
    question = (
        f"The user asked: {user_context.strip()}\nDescribe the attached photo."
        if user_context.strip()
        else "Describe the attached photo."
    )
    completion = runtime.complete(
        [
            RuntimeMessage(role="system", content=_DESCRIBE_SYSTEM),
            RuntimeMessage(role="user", content=question, image_data_url=image_data_url),
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    return completion.text.strip()
