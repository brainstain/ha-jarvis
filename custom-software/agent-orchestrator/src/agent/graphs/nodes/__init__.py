"""Concrete node callables for LangGraph workflows.

Each ``make_*`` function closes over live dependencies (LLMClient, MCPToolRegistry,
ScopedMemory, SafetyGuard) and returns a bound async callable suitable for injection
into build_simple_graph, build_multistep_graph, build_interactive_graph, etc.

This keeps graph structure files (simple.py, multistep.py, …) free of import-time
dependency on live services, which is what makes them unit-testable with fakes.
"""

from agent.graphs.nodes.memory import make_memory_lookup
from agent.graphs.nodes.planning import make_planner
from agent.graphs.nodes.synthesis import make_synthesizer
from agent.graphs.nodes.tools import make_parallel_executor, make_tool_executor

__all__ = [
    "make_memory_lookup",
    "make_planner",
    "make_synthesizer",
    "make_tool_executor",
    "make_parallel_executor",
]
