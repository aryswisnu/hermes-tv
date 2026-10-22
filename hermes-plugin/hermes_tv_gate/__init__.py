"""hermes-tv-gate - refuse every tool on API-server runs.

Every API-server run comes from the Hermes TV bridge, and each one is a background run that needs
no tool: the picks, YouTube and schedule prompts carry text from strangers (YouTube titles).
The X-Hermes-Session-Key header never reaches pre_tool_call (the approval session key is the
run id), so the gate keys on the session platform.
"""

import logging

GATED_PLATFORM = "api_server"
BLOCK = {
    "action": "block",
    "message": "Hermes TV runs have no tools. Answer from what you know, in the JSON block the request asks for.",
}


def decide(tool_name, platform):
    if platform == GATED_PLATFORM:
        return BLOCK
    return None


def _current_platform():
    # Imported at call time so the host tests run without Hermes installed.
    from gateway.session_context import get_session_env
    return get_session_env("HERMES_SESSION_PLATFORM", "")


def register(ctx):
    def _gate(tool_name="", **_):
        try:
            return decide(tool_name, _current_platform())
        except Exception:
            # The platform is unknown, so this may be an API-server run: fail closed.
            logging.getLogger(__name__).exception("hermes-tv-gate: platform lookup failed; %s blocked", tool_name)
            return BLOCK

    ctx.register_hook("pre_tool_call", _gate)
