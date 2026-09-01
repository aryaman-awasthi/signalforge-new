"""The agent roster (REQ-052).

Every agent here is a function from gathered evidence to *proposals*. None holds
a repository, a bus, a tool or an invoker: the governor writes, the governor
publishes, and there is nothing on this interface that can execute anything.
"""

from signalforge.application.brain.agents.base import (
    Agent,
    AgentOutcome,
    EdgeProposal,
    ModelBackedAgent,
    NodeProposal,
)
from signalforge.application.brain.agents.change import ChangeIntelligenceAgent
from signalforge.application.brain.agents.communication import JoinBriefWriter
from signalforge.application.brain.agents.conversation import ConversationAgent
from signalforge.application.brain.agents.copilot import CopilotAgent
from signalforge.application.brain.agents.historical import HistoricalLearningAgent
from signalforge.application.brain.agents.hypothesis import HypothesisAgent
from signalforge.application.brain.agents.telemetry import TelemetryAgent
from signalforge.application.brain.agents.topology import TopologyAgent

__all__ = [
    "Agent",
    "AgentOutcome",
    "ChangeIntelligenceAgent",
    "ConversationAgent",
    "CopilotAgent",
    "EdgeProposal",
    "HistoricalLearningAgent",
    "HypothesisAgent",
    "JoinBriefWriter",
    "ModelBackedAgent",
    "NodeProposal",
    "TelemetryAgent",
    "TopologyAgent",
]
