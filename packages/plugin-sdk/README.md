# JoeOS Plugin SDK

A Python SDK for building JoeOS plugins. Plugins declare a versioned manifest,
register typed contributions, and implement a bounded entry handler that runs
inside the isolated Extension Host. The SDK is independent from JoeOS core
implementation details.

## Layout

- `joesdk/` - the SDK package.
- `templates/` - minimal plugin templates (command, panel, tool, agent role,
  parser, document importer, provider, theme).
- `tests/` - SDK tests.

## Quick start

```bash
python -m joesdk create my.hello ~/plugins/hello
python -m joesdk validate ~/plugins/hello
python -m joesdk package ~/plugins/hello -o hello.zip
python -m joesdk install hello.zip
```

A plugin's entry point implements:

```python
def handle(method, params, api):
    if method == "lifecycle.activate":
        return {"activated": True}
    if method == "contribution.invoke":
        return {"ok": True}
    return {"ok": True}
```

`api` is a bounded object; every capability it exposes is brokered by the
Extension Host against the plugin's granted permissions. Plugins never import
core JoeOS internals.
