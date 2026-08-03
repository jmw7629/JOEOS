"""Panel demo plugin entry point."""


def handle(method, params, api):
    if method == "lifecycle.activate":
        return {"activated": True, "panel_id": "publisher.panel-demo.status"}
    if method == "contribution.invoke":
        return {"rendered": True}
    return {"ok": True}
