"""GUI package for Linux Theme Manager (Tkinter)."""


from ..dependencies import ensure_gui_dependencies


def launch_gui() -> None:
    if not ensure_gui_dependencies(auto_install=True, require_pillow=True):
        raise RuntimeError("GUI dependencies are not available (tkinter/Pillow)")
    from .app import ThemeManagerApp
    app = ThemeManagerApp()
    app.mainloop()
