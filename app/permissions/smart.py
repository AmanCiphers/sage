"""Smart guardian LLM for SAGE permission system.

Uses Nebius DeepSeek-V4-Flash (fast, low-cost) to assess dangerous commands.
Returns: APPROVE | DENY | ESCALATE
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

from app.inference.manager import InferenceManager
from app.inference.models import InferenceRequest, ModelRequirements
from app.permissions.config import get_permission_config
from app.permissions.models import SmartVerdict

logger = logging.getLogger(__name__)


# Guardian prompt - optimized for speed and accuracy
GUARDIAN_PROMPT = """You are SAGE's security guardian. Assess if this command/action is genuinely dangerous.

Context:
- Command: {command}
- Description: {description}
- Detected patterns: {patterns}
- User intent: {intent}

Respond with EXACTLY one word:
- APPROVE: benign, standard operation, or user clearly knows what they're doing
- DENY: genuinely dangerous (credential theft, destructive, injection, etc.)
- ESCALATE: uncertain, needs human judgment

Do NOT explain. One word only."""


@dataclass
class GuardianAssessment:
    verdict: str  # "APPROVE" | "DENY" | "ESCALATE"
    reasoning: str = ""
    confidence: float = 1.0


class SmartGuardian:
    """Smart guardian LLM for assessing dangerous commands."""
    
    def __init__(self, inference_manager: InferenceManager | None = None):
        self.config = get_permission_config()
        self.inference_manager = inference_manager
        self._enabled = self.config.smart_approval
        
        # Circuit breaker state
        self._denial_tally: dict[str, int] = {}
    
    @property
    def enabled(self) -> bool:
        return self._enabled and self.inference_manager is not None
    
    def set_inference_manager(self, inference_manager: InferenceManager) -> None:
        self.inference_manager = inference_manager
    
    def enable(self) -> None:
        self._enabled = True
    
    def disable(self) -> None:
        self._enabled = False
    
    async def assess(
        self,
        command: str,
        description: str,
        pattern_keys: list[str],
        intent: str = "",
    ) -> tuple[str, str, float]:  # (verdict, reasoning, confidence)
        """Assess a command using the guardian LLM.
        
        Returns: (verdict, reasoning, confidence)
        verdict: "APPROVE" | "DENY" | "ESCALATE"
        """
        if not self.enabled:
            return "ESCALATE", "Smart guardian disabled", 0.0
        
        if not self.inference_manager:
            logger.warning("Smart guardian enabled but no inference manager available")
            return "ESCALATE", "No inference manager", 0.0
        
        # Check circuit breaker
        if self._is_circuit_breaker_tripped():
            logger.warning("Circuit breaker tripped - denying")
            return "DENY", "Circuit breaker tripped", 1.0
        
        # Build prompt
        patterns_str = ", ".join(pattern_keys) if pattern_keys else "none"
        prompt = GUARDIAN_PROMPT.format(
            command=command[:2000] if command else "none",
            description=description[:500] if description else "none",
            patterns=patterns_str,
            intent=intent[:200] if intent else "unknown",
        )
        
        try:
            request = InferenceRequest(
                messages=[{"role": "user", "content": prompt}],
                temperature=self.config.smart_temperature,
                max_tokens=self.config.smart_max_tokens,
            )
            
            response = await asyncio.wait_for(
                self.inference_manager.generate(
                    request,
                    requirements=ModelRequirements(tool_calling=False),
                ),
                timeout=10.0,
            )
            
            verdict = self._parse_verdict(response.content)
            confidence = 0.9 if verdict in ("APPROVE", "DENY") else 0.5
            
            if verdict == "DENY":
                self._record_denial()
            
            return verdict, response.content.strip(), confidence
            
        except asyncio.TimeoutError:
            logger.warning("Smart guardian timed out")
            return "ESCALATE", "Timeout", 0.0
        except Exception as e:
            logger.warning(f"Smart guardian error: {e}")
            return "ESCALATE", f"Error: {e}", 0.0
    
    def _parse_verdict(self, response: str) -> str:
        """Parse guardian response to extract verdict."""
        if not response:
            return "ESCALATE"
        
        # Clean response
        cleaned = response.strip().upper()
        
        # Extract just the verdict word
        for verdict in ["APPROVE", "DENY", "ESCALATE"]:
            if verdict in cleaned:
                return verdict
        
        return "ESCALATE"
    
    def _record_denial(self) -> None:
        """Record a denial for circuit breaker."""
        # Simple per-process tally
        self._denial_count = getattr(self, '_denial_count', 0) + 1
    
    def _is_circuit_breaker_tripped(self) -> bool:
        threshold = get_permission_config().denial_breaker_threshold
        if threshold <= 0:
            return False
        return getattr(self, '_denial_count', 0) >= threshold
    
    def reset_circuit_breaker(self) -> None:
        self._denial_count = 0
    
    @property
    def denial_count(self) -> int:
        return getattr(self, '_denial_count', 0)


# Global instance
_smart_guardian: "SmartGuardian | None" = None


def get_smart_guardian(inference_manager: "InferenceManager | None" = None) -> "SmartGuardian":
    """Get or create the global smart guardian instance."""
    global _smart_guardian
    if _smart_guardian is None:
        _smart_guardian = SmartGuardian(inference_manager)
    elif inference_manager and not _smart_guardian.inference_manager:
        _smart_guardian.set_inference_manager(inference_manager)
    return _smart_guardian


def reset_smart_guardian() -> None:
    """Reset the global smart guardian (for testing)."""
    global _smart_guardian
    _smart_guardian = None