"""Proactive behavior engine (ArchitectureV2 §56)."""

from datetime import datetime
from typing import Any

from app.events.bus import EventBus
from app.events.models import Event
from app.execution.engine import ExecutionEngine
from app.observability.logging import get_logger
from app.scheduler.models import ProactiveRule
from app.scheduler.store import SchedulerStore
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)


class ProactiveEngine:
    """Evaluates proactive rules and triggers actions (ArchitectureV2 §56)."""

    def __init__(
        self,
        store: SchedulerStore,
        event_bus: EventBus,
        execution_engine: ExecutionEngine | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.store = store
        self.event_bus = event_bus
        self.execution_engine = execution_engine
        self.tool_registry = tool_registry
        self._rule_cache: dict[str, ProactiveRule] = {}
        self._cache_valid = False

    async def initialize(self) -> None:
        """Load rules into cache."""
        await self._refresh_cache()

    async def _refresh_cache(self) -> None:
        rules = await self.store.list_rules(enabled_only=True)
        self._rule_cache = {r.rule_id: r for r in rules}
        self._cache_valid = True
        logger.debug("proactive_cache_refreshed", rule_count=len(rules))

    async def handle_event(self, event: Event) -> None:
        """Process an incoming event against proactive rules."""
        if not self._cache_valid:
            await self._refresh_cache()

        matching_rules = [
            rule for rule in self._rule_cache.values()
            if rule.trigger_event_type == event.event_type
        ]

        for rule in matching_rules:
            await self._evaluate_rule(rule, event)

    async def _evaluate_rule(self, rule: ProactiveRule, event: Event) -> None:
        """Evaluate a rule's condition and fire if true."""
        # Check cooldown
        if rule.last_fired_at:
            try:
                last_fired = datetime.fromisoformat(rule.last_fired_at.replace("Z", "+00:00"))
                now = datetime.utcnow()
                elapsed = (now - last_fired).total_seconds()
                if elapsed < rule.cooldown_seconds:
                    logger.debug("rule_cooldown_active", rule_id=rule.rule_id, elapsed=elapsed)
                    return
            except (ValueError, TypeError):
                pass

        # Evaluate condition
        try:
            # Safe evaluation context
            context = {
                "event": event,
                "payload": event.payload,
                "event_type": event.event_type,
                "source": event.source,
                "timestamp": event.timestamp,
            }

            # The condition is a Python expression returning bool
            result = eval(rule.condition, {"__builtins__": {}}, context)

            if not result:
                logger.debug("rule_condition_false", rule_id=rule.rule_id)
                return

        except Exception as exc:
            logger.warning("rule_evaluation_error", rule_id=rule.rule_id, error=str(exc))
            return

        # Condition passed - fire the action
        await self._fire_rule(rule, event)

    async def _fire_rule(self, rule: ProactiveRule, triggering_event: Event) -> None:
        """Execute the rule's action by publishing an event AND executing the tool if applicable."""
        now = datetime.utcnow().isoformat() + "Z"

        # Build action payload with template + event context
        action_payload = {
            "rule_id": rule.rule_id,
            "rule_name": rule.name,
            "triggered_by": triggering_event.event_id,
            "trigger_event_type": triggering_event.event_type,
            "fired_at": now,
            **rule.action_payload_template,
        }

        # Add event payload for context
        action_payload["trigger_payload"] = triggering_event.payload

        # Check if action_event_type is a registered tool - if so, execute it directly
        if self.tool_registry and self.execution_engine:
            try:
                # If it's a known tool, execute it directly
                from app.execution.models import ToolCall
                result = await self.execution_engine.execute(
                    ToolCall(tool_name=rule.action_event_type, arguments=rule.action_payload_template)  # noqa: E501
                )
                logger.info("proactive_rule_executed_tool", rule_id=rule.rule_id, name=rule.name, tool=rule.action_event_type, success=result.success)  # noqa: E501
            except KeyError:
                # Not a known tool - fall back to event publishing
                pass
            except Exception as exc:
                logger.warning("proactive_tool_execution_failed", rule_id=rule.rule_id, error=str(exc))  # noqa: E501

        # Always publish the event for logging/other consumers
        event = self.event_bus.create_event(
            event_type=rule.action_event_type,
            source="proactive",
            payload=action_payload,
            correlation_id=triggering_event.correlation_id or triggering_event.event_id,
        )

        await self.event_bus.publish(event)

        # Update rule state
        rule.last_fired_at = now
        rule.fire_count += 1
        await self.store.update_rule(rule)

        logger.info("proactive_rule_fired", rule_id=rule.rule_id, name=rule.name)

    async def add_rule(
        self,
        name: str,
        description: str,
        trigger_event_type: str,
        condition: str,
        action_event_type: str,
        action_payload_template: dict[str, Any] | None = None,
        cooldown_seconds: int = 300,
    ) -> str:
        """Create a new proactive rule."""
        rule = ProactiveRule(
            name=name,
            description=description,
            trigger_event_type=trigger_event_type,
            condition=condition,
            action_event_type=action_event_type,
            action_payload_template=action_payload_template or {},
            cooldown_seconds=cooldown_seconds,
        )

        await self.store.create_rule(rule)
        await self._refresh_cache()
        logger.info("proactive_rule_created", rule_id=rule.rule_id, name=name)
        return rule.rule_id

    async def remove_rule(self, rule_id: str) -> bool:
        result = await self.store.delete_rule(rule_id)
        if result:
            await self._refresh_cache()
        return result

    async def enable_rule(self, rule_id: str, enabled: bool = True) -> bool:
        rule = await self.store.get_rule(rule_id)
        if not rule:
            return False
        rule.enabled = enabled
        await self.store.update_rule(rule)
        await self._refresh_cache()
        return True

    async def list_rules(self) -> list[ProactiveRule]:
        if not self._cache_valid:
            await self._refresh_cache()
        return list(self._rule_cache.values())

    async def get_rule(self, rule_id: str) -> ProactiveRule | None:
        if not self._cache_valid:
            await self._refresh_cache()
        return self._rule_cache.get(rule_id) or await self.store.get_rule(rule_id)
