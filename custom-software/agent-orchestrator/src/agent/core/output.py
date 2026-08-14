"""Output routing — decides where a result is delivered."""

from __future__ import annotations

from agent.api.schemas import ChatRequest, RoutingDecision

# Source interface -> its natural synchronous reply channel.
SOURCE_DEFAULT_CHANNEL = {
    "voice": "voice",
    "ha": "voice",
    "webui": "webui",
    "notification": "push",
}


class OutputRouter:
    """Chooses the delivery channel for a response."""

    @staticmethod
    def resolve(request: ChatRequest, decision: RoutingDecision) -> str:
        """Return the channel a response should be delivered on.

        Async work can't answer inline on a voice satellite that has already
        stopped listening, so anything async is pushed instead.
        """
        if decision.execution_mode == "async":
            return "push"
        return SOURCE_DEFAULT_CHANNEL.get(request.source, decision.output_channel)

    @staticmethod
    def is_speech(channel: str) -> bool:
        """Voice replies need shorter, plainer text than screen replies."""
        return channel == "voice"
