"""
Vertically-scrollable frame using a Canvas underneath.

Usage
-----
    sf = ScrolledFrame(parent)
    sf.pack(fill="both", expand=True)
    ttk.Label(sf.inner, text="Hello").pack()   # add widgets to sf.inner
"""

import tkinter as tk
from tkinter import ttk


class ScrolledFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget, bg: str = "#f0f2f5", **kwargs) -> None:
        super().__init__(parent, **kwargs)

        self._bg = bg
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=bg)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = ttk.Frame(self.canvas)
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        # Bind scroll only while the pointer is inside the canvas
        self.canvas.bind("<Enter>", self._bind_scroll)
        self.canvas.bind("<Leave>", self._unbind_scroll)

    # ── internal event handlers ────────────────────────────────────────────────

    def _on_inner_resize(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._win_id, width=event.width)

    def _bind_scroll(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>",   self._scroll_up)
        self.canvas.bind_all("<Button-5>",   self._scroll_down)

    def _unbind_scroll(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _scroll_up(self, _event: tk.Event) -> None:
        self.canvas.yview_scroll(-1, "units")

    def _scroll_down(self, _event: tk.Event) -> None:
        self.canvas.yview_scroll(1, "units")

    # ── public helpers ─────────────────────────────────────────────────────────

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)

    def clear(self) -> None:
        """Destroy all children of the inner frame."""
        for widget in self.inner.winfo_children():
            widget.destroy()
