"""Independent Qt preview entry; the existing main.py still starts Tk."""
from quest_tool.qt_ui.bootstrap import run


if __name__ == "__main__":
    raise SystemExit(run())
