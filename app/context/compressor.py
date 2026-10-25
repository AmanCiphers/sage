"""Context compaction for the forever-running chat.

Adapted from Hermes' context_compressor, with protected head/tail (v2):

    threshold reached
      -> anti-thrash check (2 ineffective strikes block further attempts)
      -> protected head: first system, first human, first assistant, first tool
      -> protected tail: last N messages (configurable, default 4)
      -> middle is merged into one iterative summary via a single LLM call
         (previous summary + new middle; never re-summarized from scratch)
      -> boundary snapping: never split tool_call/tool_result pairs
      -> deterministic fallback summary if the LLM call fails
      -> new history = protected head + summary row + protected tail

Raw history in SQLite is never modified; compaction only changes the view.
"""

import logging

from app.context.tokens import (
    apply_calibration,
    calibrate_ratio,
    estimate_history_tokens,
    estimate_message_tokens,
)
from app.inference.manager import InferenceManager
from app.inference.models import InferenceRequest, ModelRequirements
from app.persistence.store import SessionStateStore

logger = logging.getLogger(__name__)

MAX_STRIKES = 2
SUMMARY_TARGET_CHARS = 4000

# Protected region configuration
PROTECTED_HEAD_COUNT = 4  # system, first human, first assistant, first tool
PROTECTED_TAIL_COUNT = 4  # last N messages

SUMMARY_PREFIX = (
    "[Context summary of earlier conversation. "
    "This is background context, not new user input.]"
)
SUMMARY_SUFFIX = "--- END OF CONTEXT SUMMARY ---"


def tail_budget_tokens(context_window: int) -> int:
    """Tail keeps 2.5% of the window, bounded to [2000, 8000] tokens."""

    return max(2000, min(8000, context_window // 40))


def summary_row(summary: str) -> dict:
    return {
        "role": "user",
        "content": f"{SUMMARY_PREFIX}\n{summary}\n{SUMMARY_SUFFIX}",
    }


class ContextCompressor:
    def __init__(
        self,
        inference_manager: InferenceManager,
        session_state: SessionStateStore,
        context_window: int,
        threshold_ratio: float = 0.5,
        calibration_ratio: float = 1.0,
    ) -> None:
        self.inference_manager = inference_manager
        self.session_state = session_state
        self.context_window = context_window
        self.threshold_tokens = int(context_window * threshold_ratio)
        self.tail_budget = tail_budget_tokens(context_window)
        self.calibration_ratio = calibration_ratio

    def needs_compaction(self, messages: list[dict]) -> bool:
        estimated = estimate_history_tokens(messages)
        calibrated = apply_calibration(estimated, self.calibration_ratio)
        return calibrated >= self.threshold_tokens

    def update_calibration(self, estimated: int, actual: int) -> None:
        """Update calibration ratio from a real provider response.

        Blends the new ratio into the running average (weight 0.3).
        """
        if estimated <= 0 or actual <= 0:
            return
        new_ratio = calibrate_ratio(estimated, actual)
        self.calibration_ratio = 0.7 * self.calibration_ratio + 0.3 * new_ratio

    def extract_tail(self, messages: list[dict]) -> list[dict]:
        """Protected tail: last N messages (PROTECTED_TAIL_COUNT), respecting token budget.

        Anchors: the newest message is always kept; an assistant tool_call
        message is never separated from its tool results.
        """

        tail: list[dict] = []
        used = 0

        for message in reversed(messages):
            cost = apply_calibration(estimate_message_tokens(message), self.calibration_ratio)

            if tail and used + cost > self.tail_budget:
                break

            tail.append(message)
            used += cost

        tail.reverse()

        # Never start the tail mid tool-group: drop a leading assistant
        # tool_call message whose results were not fully included.
        while tail and self._is_tool_call_message(tail[0]):
            tool_call_ids = {
                call.get("id")
                for call in tail[0].get("tool_calls") or []
            }
            result_ids = {
                m.get("tool_call_id")
                for m in tail[1:]
                if m.get("role") == "tool"
            }
            if tool_call_ids <= result_ids:
                break
            tail.pop(0)

        # Ensure we have at least PROTECTED_TAIL_COUNT messages (boundary snap)
        if len(tail) < PROTECTED_TAIL_COUNT and len(messages) >= PROTECTED_TAIL_COUNT:
            # Take the last PROTECTED_TAIL_COUNT messages
            tail = messages[-PROTECTED_TAIL_COUNT:]

        return tail

    def extract_head(self, messages: list[dict]) -> list[dict]:
        """Protected head: first system, first human, first assistant, first tool message.

        Returns up to PROTECTED_HEAD_COUNT messages that form the protected head.
        """
        head: list[dict] = []
        seen_roles = set()

        for message in messages:
            role = message.get("role", "")
            if role and role not in seen_roles:
                seen_roles.add(role)
                head.append(message)
                if len(head) >= PROTECTED_HEAD_COUNT:
                    break

        return head

    def _snap_boundary(self, messages: list[dict], start_idx: int, end_idx: int) -> tuple[int, int]:
        """Snap boundaries to not split tool_call/tool_result pairs.

        Returns (start_idx, end_idx) adjusted to keep tool pairs together.
        """
        # Snap start_idx backward to not cut into a tool_call that has results after
        while start_idx > 0:
            msg = messages[start_idx - 1]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Check if any tool results follow
                tool_call_ids = {
                    call.get("id")
                    for call in msg.get("tool_calls") or []
                }
                result_ids = {
                    m.get("tool_call_id")
                    for m in messages[start_idx:end_idx]
                    if m.get("role") == "tool"
                }
                if tool_call_ids & result_ids:
                    # This tool_call has results in the middle, move start before it
                    start_idx -= 1
                    continue
            break

        # Snap end_idx forward to not cut off tool results
        while end_idx < len(messages):
            msg = messages[end_idx]
            if msg.get("role") == "tool":
                # This is a tool result, check if its call is in the middle
                call_id = msg.get("tool_call_id")
                if call_id:
                    # Check if the corresponding tool_call is in the middle
                    call_in_middle = any(
                        call_id in {
                            call.get("id")
                            for call in m.get("tool_calls") or []
                        }
                        for m in messages[start_idx:end_idx]
                        if m.get("role") == "assistant"
                    )
                    if call_in_middle:
                        end_idx += 1
                        continue
            break

        return start_idx, end_idx

    @staticmethod
    def _is_tool_call_message(message: dict) -> bool:
        return (
            message.get("role") == "assistant"
            and bool(message.get("tool_calls"))
        )

    async def compress(self, messages: list[dict]) -> list[dict]:
        """Return a compacted view of `messages` (protected head + summary row + protected tail)."""

        state = await self.session_state.get()

        if state["ineffective_strikes"] >= MAX_STRIKES:
            logger.warning("compaction_blocked strikes=%s", state["ineffective_strikes"])
            return messages

        previous_summary = state["summary"]
        conversation = [m for m in messages if not _is_summary_row(m)]

        # Extract protected head and tail
        head = self.extract_head(conversation)
        tail = self.extract_tail(conversation)

        # Determine middle region (between head and tail)
        head_len = len(head)
        tail_len = len(tail)
        middle = conversation[head_len:len(conversation) - tail_len] if tail_len > 0 else conversation[head_len:]

        if not middle:
            return messages

        # Snap boundaries to not split tool pairs
        head_len, middle_end = self._snap_boundary(conversation, head_len, head_len + len(middle))
        middle = conversation[head_len:middle_end]
        tail = conversation[middle_end:]

        new_summary = await self._summarize(previous_summary, middle)

        compacted = [*head, summary_row(new_summary), *tail]
        effective = estimate_history_tokens(compacted) < self.threshold_tokens

        strikes = 0 if effective else state["ineffective_strikes"] + 1
        await self.session_state.save_summary(new_summary)
        await self.session_state.set_strikes(strikes)

        logger.info(
            "compaction_complete effective=%s strikes=%s middle_messages=%s head=%s tail=%s",
            effective,
            strikes,
            len(middle),
            len(head),
            len(tail),
        )

        return compacted

    async def _summarize(
        self,
        previous_summary: str | None,
        middle: list[dict],
    ) -> str:
        transcript = self._format_middle(middle)

        prompt = f"""You maintain a running summary of a long conversation so work can continue after older messages leave the context window.

Previous summary (may be empty):
{previous_summary or "(none)"}

New conversation segment:
{transcript}

Merge the previous summary with the new segment into one updated summary.
Cover: current goal, active state, key decisions, errors/fixes, relevant files or resources.
Keep it under {SUMMARY_TARGET_CHARS} characters. Output only the summary text."""  # noqa: E501

        try:
            response = await self.inference_manager.generate(
                InferenceRequest(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=1000,
                ),
                requirements=ModelRequirements(reasoning=True),
            )

            summary = response.content.strip()

            if not summary:
                raise ValueError("empty summary")

            return summary[: SUMMARY_TARGET_CHARS * 2]

        except Exception:
            logger.warning("summary_llm_failed using_deterministic_fallback")
            return self._fallback_summary(previous_summary, middle)

    def _format_middle(self, middle: list[dict]) -> str:
        lines = []

        for message in middle:
            role = message.get("role", "?")
            content = (message.get("content") or "")[:500]
            tool_calls = message.get("tool_calls")

            if tool_calls:
                names = [
                    call.get("function", {}).get("name", "?")
                    for call in tool_calls
                ]
                lines.append(f"{role}: called tools {', '.join(names)}")
            elif role == "tool":
                lines.append(f"tool result: {content[:200]}")
            elif content:
                lines.append(f"{role}: {content}")

            if len("\n".join(lines)) > 12000:
                lines.append("... (segment truncated)")
                break

        return "\n".join(lines)

    @staticmethod
    def _fallback_summary(
        previous_summary: str | None,
        middle: list[dict],
    ) -> str:
        """Deterministic summary when the summarizer LLM is unavailable."""

        user_lines = [
            (m.get("content") or "")[:200]
            for m in middle
            if m.get("role") == "user" and m.get("content")
        ]

        recent = "\n".join(f"- {line}" for line in user_lines[-10:])

        if previous_summary:
            return f"{previous_summary}\n\nRecent user requests:\n{recent}"

        return f"Recent user requests:\n{recent}"


def _is_summary_row(message: dict) -> bool:
    """Identify the summary row inside a built history."""

    return (message.get("content") or "").startswith(SUMMARY_PREFIX)
