"""Mesh Sentinel — non-destructive mesh inspection for Autodesk Maya."""

__version__ = "1.7.1-rc1"


def show():
    """Open the singleton inspector in Maya."""
    from .ui import show as open_window
    return open_window()


def reload_ui():
    """Reload a previous release in an already running Maya session."""
    import importlib
    import sys
    old = sys.modules.get(__name__ + ".ui")
    window = getattr(old,"_window",None)
    if window is not None:
        if window.worker is not None or window.extracting or (hasattr(window,'live') and window.live.worker is not None):
            raise RuntimeError("検査が終了してから更新してください。")
        window.close()
        if hasattr(window,'hover'):
            window.hover.dispose()
        if hasattr(window,'live'):
            window.live.dispose()
        window.deleteLater()
    viewport = sys.modules.get(__name__ + ".viewport")
    if viewport is not None:
        viewport.SESSION.clear()
        import maya.cmds as cmds
        if cmds.pluginInfo(viewport.PLUGIN_NAME, query=True, loaded=True):
            cmds.unloadPlugin(viewport.PLUGIN_NAME)
    # Reload in place so the loaded draw plug-in keeps its module reference,
    # while callbacks and the session implementation are replaced with the fix.
    for name in ("model","geometry","engine","exporter","maya_bridge","repair","repair_ui","bulk_repair","bulk_ui","uv_topology","uv_repair","uv_ui","overlay_data","hover_pick","viewport","sentinel_overlay_plugin","hover_ui","live_review","ui_scaling","ui"):
        module = sys.modules.get(__name__ + "." + name)
        if module is not None:
            importlib.reload(module)
    return show()
