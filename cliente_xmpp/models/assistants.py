from __future__ import annotations

import re
from dataclasses import dataclass

from cliente_xmpp.models.chat import Message

# WhatsApp assistants (Meta AI and its personas) are exposed by the bridge under
# the phone aliases WhatsApp reserves for bots (mirrors whatsmeow's IsBot rule)
# or, when they have none (e.g. Muse), as "+bot.<id>".
_META_AI_LOCALPART = re.compile(r"^\+(?:1313555\d{4}|131655500\d{2}|bot\.\d{1,24})$")


def is_meta_ai_assistant_jid(jid: str) -> bool:
    """Return True for a bridge contact that addresses a WhatsApp assistant."""
    bare = (jid or "").split("/", 1)[0]
    local, separator, domain = bare.partition("@")
    return bool(separator and domain) and bool(_META_AI_LOCALPART.fullmatch(local))


# The bridge appends suggested follow-ups to an assistant answer as a final
# "Sugerencias:" list of "• " items (see the bridge's canComposeBotAnswer).
MAX_ASSISTANT_SUGGESTIONS = 8
MAX_ASSISTANT_SUGGESTION_LENGTH = 300
_SUGGESTIONS_HEADER = "\n\nSugerencias:\n"
_SUGGESTION_BULLET = "• "


@dataclass(frozen=True, slots=True)
class AssistantApproval:
    """A pending Muse approval request, as rendered by the bridge."""

    reference: str  # the bridge's approval ID, taken from the message ID
    title: str
    options: tuple[str, ...]


APPROVAL_COMMAND = "/muse-decidir"
# The bridge publishes each approval with this message ID prefix (see the
# bridge's canApprovalMessageIDPrefix); the rest is the approval ID.
_APPROVAL_MESSAGE_ID_PREFIX = "can-muse-approval-"
_APPROVAL_MAX_REFERENCE = 256
_APPROVAL_TITLE = "Solicitud de aprobación de Muse"
_APPROVAL_OPTIONS_HEADER = "\n\nOpciones:\n"
_APPROVAL_OPTION = re.compile(r"^(\d{1,2})\. (.+)$")


def assistant_approval(message: Message | None) -> AssistantApproval | None:
    """Return the actionable approval of a received Muse message, if still pending.

    Decided, resolved or malformed requests are never actionable.
    """
    if (
        message is None
        or message.outgoing
        or message.retracted
        or not is_meta_ai_assistant_jid(message.chat_jid)
    ):
        return None
    body = message.body or ""
    if not body.startswith(_APPROVAL_TITLE) or "\nDecidido: " in body:
        return None
    state = re.search(r"^Estado: (.+)$", body, re.MULTILINE)
    if state is not None and state.group(1).strip() != "pendiente":
        return None
    message_id = message.message_id or ""
    reference = message_id.removeprefix(_APPROVAL_MESSAGE_ID_PREFIX)
    if (
        not message_id.startswith(_APPROVAL_MESSAGE_ID_PREFIX)
        or not reference
        or len(reference) > _APPROVAL_MAX_REFERENCE
        or any(character.isspace() for character in reference)
    ):
        return None
    start = body.find(_APPROVAL_OPTIONS_HEADER)
    if start < 0:
        return None
    options: list[str] = []
    for line in body[start + len(_APPROVAL_OPTIONS_HEADER):].split("\n"):
        match = _APPROVAL_OPTION.match(line)
        if match is None:
            break
        if int(match.group(1)) != len(options) + 1:
            return None  # numbering must be exact: the number is what gets sent
        options.append(" ".join(match.group(2).split()))
    if not options or len(options) > MAX_ASSISTANT_SUGGESTIONS:
        return None
    title = body.split("\n", 1)[0]
    return AssistantApproval(reference=reference, title=title, options=tuple(options))


def approval_command(approval: AssistantApproval, option_index: int) -> str:
    """Build the local bridge command for the 1-based option of an approval."""
    if not 0 <= option_index < len(approval.options):
        raise ValueError("Opción de aprobación fuera de rango")
    return f"{APPROVAL_COMMAND} {option_index + 1} {approval.reference}"


def assistant_suggestions(message: Message | None) -> tuple[str, ...]:
    """Return the follow-up prompts offered by a received assistant answer."""
    if (
        message is None
        or message.outgoing
        or message.retracted
        or not is_meta_ai_assistant_jid(message.chat_jid)
    ):
        return ()
    body = message.body or ""
    start = body.rfind(_SUGGESTIONS_HEADER)
    if start < 0:
        return ()
    prompts: list[str] = []
    for line in body[start + len(_SUGGESTIONS_HEADER):].split("\n"):
        if not line.startswith(_SUGGESTION_BULLET):
            # The list closes the answer; anything else means it is not ours.
            return ()
        prompt = " ".join(line.removeprefix(_SUGGESTION_BULLET).split())
        if prompt and prompt not in prompts and len(prompt) <= MAX_ASSISTANT_SUGGESTION_LENGTH + 1:
            prompts.append(prompt)
        if len(prompts) >= MAX_ASSISTANT_SUGGESTIONS:
            break
    return tuple(prompts)
