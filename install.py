"""Drag this file into a Maya viewport to launch Mesh Sentinel."""
import os
import sys


def onMayaDroppedPythonFile(*args, **kwargs):
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    import mesh_sentinel
    import importlib
    importlib.reload(mesh_sentinel)
    return mesh_sentinel.reload_ui()


if __name__ == "__main__":
    onMayaDroppedPythonFile()
