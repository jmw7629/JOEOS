"""%s demo plugin entry point."""


def handle(method, params, api):
    if method == "lifecycle.activate":
        return {"activated": True}
    if method == "contribution.invoke":
        return {"ok": True}
    return {"ok": True}
