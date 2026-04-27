"""Input / output guardrails for the Solana chat (OpenRouter)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

INPUT_SYSTEM = """You classify user messages for a read-only Solana helper.
The backend can only answer using tools: native SOL balance (when a pubkey is given),
SPL token balance for a given mint, SPL token account count, recent transaction signatures
for an address, transaction details by signature, account info, and basic network (slot/epoch).
There is no server default wallet; the user must supply addresses when needed for *new* lookups.

You may receive "Earlier in this session" text. If the user only asks to recall their *own* name,
wallet address, or words they *already typed* in those prior turns (e.g. "what's my name?",
"what address did I give?"), that is ALWAYS allowed=true — they are not asking you to spy on
strangers; they are asking about this same conversation. Only set allowed=false for true
out-of-scope or harmful requests.

If the user asks about something they said earlier in this same chat (e.g. which address they
pasted before), that is allowed—classify as general_solana and allowed=true.

Reply with a JSON object ONLY, no markdown:
{
  "allowed": true or false,
  "reason": "shown to the user — must sound warm, clear, and encouraging (never cold or robotic)",
  "category": "wallet" | "transaction" | "network" | "account" | "general_solana" | "out_of_scope"
}

Tone: write `reason` like a helpful teammate. Use "you" naturally. If allowed=false, gently explain
what you can do instead (e.g. try a Solana address, ask about a transaction). If allowed=true,
`reason` can be a short positive acknowledgment of what you will help with.

Set allowed=true if the user wants Solana on-chain information that our tools could help with
(including \"explain\", \"how much\", \"last txs\", \"what is this address\", devnet/mainnet context).

Set allowed=false for: other blockchains, sending/swapping/trading, writing smart contracts,
generating keys/seeds, illegal activity, extracting *others’* private data, or anything not
answerable with read-only chain data — still refuse in a friendly, respectful way and suggest a
Solana angle when possible. Do **not** block “what did I say / what address did I give earlier”
in this session; that is not personal-data extraction."""

OUTPUT_SYSTEM = """You validate and polish assistant messages for a read-only Solana chat.

The assistant only had read-only tools (no swaps, no sends). It must not claim it executed
a transaction, signed, or sent funds.

Reply with JSON ONLY, no markdown:
{
  "ok": true or false,
  "final_text": "always warm, natural, and friendly (fix tone even when ok is false)",
  "flags": ["optional e.g. claimed_execution, not_grounded"]
}

If ok is false: `final_text` should briefly apologize in a light, human way and invite the user
to ask something you can look up (addresses, tx signatures, etc.). Never be stiff or scolding.
If ok is true: `final_text` is the best version of the draft—same facts, friendlier and clearer
unless you must fix a safety issue."""

# Long base58 blobs (often conflated with private keys; Solana *transaction signatures*
# are ~87–88 base58 chars, so a blanket block caused false positives on tx details.)
_PRIVATE_KEYish = re.compile(
    r"\b[1-9A-HJ-NP-Za-km-z]{80,}\b"
)

# Short follow-ups that only make sense with prior chat; allow when prior_session_text exists.
_RECALL_SELF = re.compile(
    r"(?i)("
    r"what\s*('?s|is)\s+my\s+name|"
    r"whats\s+my\s+name|"
    r"who\s+am\s+i|"
    r"what\s*('?s|is)\s+my\s+(wallet\s+)?address|"
    r"what\s+(address|wallet)\s+did\s+i|"
    r"what\s+did\s+i\s+(say|paste|give|tell)|"
    r"remind\s+me(\s+what)?|"
    r"my\s+name\s+and|"
    r"name\s+and\s+wallet"
    r")"
)


class InputGuardResult(BaseModel):
    allowed: bool
    reason: str
    category: str = "general_solana"


class OutputGuardResult(BaseModel):
    ok: bool
    final_text: str
    flags: list[str] = Field(default_factory=list)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def run_input_guardrail(
    client: AsyncOpenAI,
    model: str,
    user_message: str,
    *,
    prior_session_text: str = "",
) -> InputGuardResult:
    if not (user_message or "").strip():
        return InputGuardResult(
            allowed=False,
            reason="Go ahead and type a question when you are ready. I'm here to help with Solana data.",
            category="out_of_scope",
        )
    if len(user_message) > 8000:
        return InputGuardResult(
            allowed=False,
            reason="That message is a bit long for us—could you shorten it and we can take another look?",
            category="out_of_scope",
        )

    prior = (prior_session_text or "").strip()
    # Same-session “what’s my name / my address” only works with prior turns; allow without the
    # classifier mis-firing on the bare question.
    if prior and len(prior) > 20 and _RECALL_SELF.match(user_message):
        return InputGuardResult(
            allowed=True,
            reason="Happy to use this chat to answer that.",
            category="general_solana",
        )

    user_block = user_message
    if prior:
        user_block = (
            f"Current message:\n{user_message}\n\n"
            f"Earlier in this same session (for follow-up questions):\n{prior[:6000]}"
        )

    res = await client.chat.completions.create(
        model=model,
        temperature=0.25,
        max_tokens=400,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": INPUT_SYSTEM},
            {"role": "user", "content": user_block},
        ],
    )
    raw = (res.choices[0].message.content or "").strip()
    d = _extract_json_object(raw) or {}
    try:
        return InputGuardResult(
            allowed=bool(d.get("allowed", False)),
            reason=str(d.get("reason", "") or "unspecified")[:500],
            category=str(d.get("category", "general_solana"))[:64],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("input guardrail parse error: %s", e)
        return InputGuardResult(
            allowed=False,
            reason="Something hiccupped on our side. Give it one more try with your question in a moment.",
            category="out_of_scope",
        )


async def run_output_guardrail(
    client: AsyncOpenAI,
    model: str,
    user_message: str,
    draft: str,
    tool_trace: str,
    *,
    prior_turns_text: str = "",
) -> OutputGuardResult:
    text = draft or ""
    if "BEGIN" in text and "PRIVATE" in text:
        return OutputGuardResult(
            ok=False,
            final_text="I can’t show that, but I’m still happy to help, paste a wallet address, transaction signature, or mint, and I’ll look up what I can on-chain (read-only).",
            flags=["sensitive_pattern"],
        )
    # Block only if the draft *introduces* a long base58 that wasn’t in the user message
    # or in tool output (e.g. tx sig returned by getTransaction). Stops 88-char sigs from
    # tripping the “sensitive” template when the user asked for those details.
    # Include prior chat so we do not treat reused addresses/sigs from history as “new” sensitive text.
    _combined = f"{user_message}\n{prior_turns_text}\n{tool_trace}"
    for m in _PRIVATE_KEYish.finditer(text):
        if m.group(0) not in _combined:
            return OutputGuardResult(
                ok=False,
                final_text="I can’t show that, but I’m still happy to help, paste a wallet address, transaction signature, or mint, and I’ll look up what I can on-chain (read-only).",
                flags=["sensitive_pattern"],
            )
    res = await client.chat.completions.create(
        model=model,
        temperature=0.25,
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": OUTPUT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"User asked:\n{user_message}\n\n"
                    f"Prior turns in this session (may be empty):\n{prior_turns_text[:8000]}\n\n"
                    f"Tool data summary (for grounding):\n{tool_trace[:12000]}\n\n"
                    f"Draft reply:\n{text[:12000]}"
                ),
            },
        ],
    )
    raw = (res.choices[0].message.content or "").strip()
    d = _extract_json_object(raw) or {}
    try:
        ok = bool(d.get("ok", True))
        final = str(d.get("final_text", text) or text)
        flags = d.get("flags")
        if not isinstance(flags, list):
            flags = []
        return OutputGuardResult(ok=ok, final_text=final[:20000], flags=[str(f) for f in flags])
    except Exception as e:  # noqa: BLE001
        log.warning("output guardrail parse error: %s", e)
        return OutputGuardResult(ok=True, final_text=text, flags=["guard_parse_error"])


def heuristic_block_output(draft: str) -> str | None:
    """Last-resort block if patterns slip through."""
    low = draft.lower()
    if "private key" in low and "0x" not in low and "public" not in low:
        if "do not" not in low and "never" not in low:
            return "I’m not the right place for private key stuff, let’s look up something on-chain together instead, like a wallet address, tx signature, or account."
    if "execute" in low and "swap" in low and "you" in low:
        return None
    return None
