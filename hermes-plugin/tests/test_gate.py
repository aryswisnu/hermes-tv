"""The Hermes TV gate: every API-server run comes from the Hermes TV bridge and is a background run
(picks, YouTube, schedule) whose prompt holds text from strangers, so no tool may run on it,
read-only or not. Other platforms run as before.
The X-Hermes-Session-Key header does not reach pre_tool_call (the approval session key is the
run id), so the gate keys on the session platform."""

import sys
import types

import hermes_tv_gate

# Hermes ignores a block directive without a message, so the message is part of the contract.
TOOLS = ["web_search", "web_extract", "read_file", "search_files", "vision_analyze", "session_search",
         "skill_view", "skills_list", "terminal", "write_file", "patch", "execute_code", "memory",
         "skill_manage", "delegate_task", "send_message", "browser_navigate", "a_tool_added_later"]


def _blocked(result):
    return (isinstance(result, dict) and result.get("action") == "block"
            and isinstance(result.get("message"), str) and result["message"].strip() != "")


def test_every_tool_blocked_on_api_server():
    for name in TOOLS:
        assert _blocked(hermes_tv_gate.decide(name, "api_server")), name


def test_read_only_tools_blocked_too():
    # A stranger's YouTube title could ask for read_file + web_extract to leak data.
    for name in ("read_file", "web_extract", "web_search"):
        assert _blocked(hermes_tv_gate.decide(name, "api_server")), name


def test_api_server_never_sent_to_approval():
    # Nobody answers an approval on a background run; "approve" would leave it waiting.
    for name in TOOLS:
        assert hermes_tv_gate.decide(name, "api_server").get("action") != "approve", name


def test_other_sessions_untouched():
    for platform in ("discord", "cli", "desktop", ""):
        for name in TOOLS:
            assert hermes_tv_gate.decide(name, platform) is None, (platform, name)


def test_register_wires_pre_tool_call_to_platform(monkeypatch):
    hooks = {}

    class Ctx:
        def register_hook(self, name, fn):
            hooks[name] = fn

    hermes_tv_gate.register(Ctx())
    monkeypatch.setattr(hermes_tv_gate, "_current_platform", lambda: "api_server")
    assert _blocked(hooks["pre_tool_call"](tool_name="write_file", args={}, session_id="s"))
    assert _blocked(hooks["pre_tool_call"](tool_name="read_file", args={}, session_id="s"))
    monkeypatch.setattr(hermes_tv_gate, "_current_platform", lambda: "discord")
    assert hooks["pre_tool_call"](tool_name="write_file", args={}, session_id="s") is None
    assert hooks["pre_tool_call"](tool_name="read_file", args={}, session_id="s") is None


def test_failed_platform_lookup_blocks(monkeypatch):
    # A broken lookup cannot tell an API-server run from another one, so the tool is refused.
    hooks = {}

    class Ctx:
        def register_hook(self, name, fn):
            hooks[name] = fn

    class SessionContext(types.ModuleType):
        def get_session_env(self, name, default=""):
            raise RuntimeError("no session context")

    monkeypatch.setitem(sys.modules, "gateway", types.ModuleType("gateway"))
    monkeypatch.setitem(sys.modules, "gateway.session_context", SessionContext("gateway.session_context"))
    hermes_tv_gate.register(Ctx())
    assert _blocked(hooks["pre_tool_call"](tool_name="read_file", args={}, session_id="s"))
