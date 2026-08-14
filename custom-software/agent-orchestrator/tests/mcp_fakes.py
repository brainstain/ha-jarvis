"""Fakes for MCP transport, shared by the client and API test modules.

These stand in for `mcp.ClientSession` and its result types so tests exercise
the hub's real caching, locking, and envelope logic without spawning
subprocesses or reaching Home Assistant over the network.
"""

import asyncio

from agent.mcp.client import MCPClientHub


class FakeTool:
    def __init__(self, name, description="", input_schema=None):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}


class CamelTool:
    """A tool as the mcp 1.x SDK spelled it, to prove both shapes are read."""

    def __init__(self, name, description="", inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {}


class FakeToolsResult:
    def __init__(self, tools):
        self.tools = tools


class TextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class ImageBlock:
    def __init__(self, data):
        self.type = "image"
        self.data = data

    def model_dump(self, **_kwargs):
        return {"type": "image", "data": self.data}


class FakeCallResult:
    def __init__(self, content=None, structured_content=None, is_error=False):
        self.content = content or []
        self.structured_content = structured_content
        self.is_error = is_error


class FakeSession:
    """Stands in for mcp.ClientSession."""

    def __init__(self, tools=None, results=None, fail_on_list=None):
        self.tools = tools or []
        self.results = results or {}
        self.fail_on_list = fail_on_list
        self.calls = []

    async def list_tools(self):
        if self.fail_on_list:
            raise self.fail_on_list
        return FakeToolsResult(self.tools)

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        outcome = self.results.get(name)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return await outcome(arguments)
        return outcome if outcome is not None else FakeCallResult([TextBlock("ok")])


class FakeHub(MCPClientHub):
    """A hub whose transport is a dict of fake sessions.

    Only `_open` is replaced, so connect()'s caching and per-server locking are
    the real implementations under test.
    """

    def __init__(self, configs, sessions=None, open_errors=None):
        super().__init__(configs)
        self.fake_sessions = sessions or {}
        self.open_errors = open_errors or {}
        self.open_count = {}

    async def _open(self, config, stack):
        self.open_count[config.name] = self.open_count.get(config.name, 0) + 1
        await asyncio.sleep(0)  # yield, so a missing lock would actually race
        if config.name in self.open_errors:
            raise self.open_errors[config.name]
        return self.fake_sessions.setdefault(config.name, FakeSession())
