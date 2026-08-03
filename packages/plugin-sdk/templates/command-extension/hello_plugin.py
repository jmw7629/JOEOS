"""Command demo plugin entry point.

Runs inside the isolated Extension Host. Never imports core internals; the
bounded ``api`` object is the only way to reach platform capabilities.
"""


def handle(method, params, api):
    if method == "lifecycle.activate":
        return {"activated": True}
    if method == "lifecycle.deactivate":
        return {"deactivated": True}
    if method == "contribution.invoke":
        contribution_id = (params or {}).get("contribution_id", "")
        if contribution_id.endswith(".hello"):
            return {"message": "Hello from the command demo plugin."}
        return {"ok": True}
    return {"ok": True}
