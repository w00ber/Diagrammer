"""HelpWindow — modeless window displaying help.md as rendered HTML.

A doc's ``##`` headings are its chapters. They are listed in a sidebar and,
for Help, in the Help menu (:func:`help_chapters`). The doc stays one
scrolling page, so the search box covers all of it; picking a chapter
scrolls to it, and the sidebar follows whichever chapter is being read.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# The shortcuts table is generated, not written in help.md, but it is a
# chapter like any other: it is appended to Help as a trailing ``<h2>``.
SHORTCUTS_CHAPTER = "Keyboard Shortcuts"

_DIM = QColor(170, 170, 170)


def find_doc(filename: str) -> Path | None:
    """Locate a doc in the package's docs/ directory.

    Every doc HelpWindow opens (help.md, tutorial.md) and the images they
    reference must live in ``src/diagrammer/docs/`` so they ship in the
    wheel. The repo-root ``docs/`` fall-back only exists for development
    checkouts and is never present in an installed package; a doc found only
    there is a packaging bug, which ``tests/test_help_window_docs.py`` guards
    against.
    """
    here = Path(__file__).resolve().parent
    for c in (here.parent / "docs" / filename,
              here.parent.parent.parent / "docs" / filename):
        if c.exists():
            return c
    return None


def doc_chapters(md_text: str) -> list[str]:
    """The ``##`` headings of a Markdown doc, in order (code fences skipped)."""
    chapters, in_code = [], False
    for line in md_text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif not in_code and line.startswith("## "):
            chapters.append(line[3:].strip())
    return chapters


def help_chapters() -> list[str]:
    """Help's chapters, for the Help menu, without opening a window."""
    path = find_doc("help.md")
    if path is None:
        return []
    return doc_chapters(path.read_text(encoding="utf-8")) + [SHORTCUTS_CHAPTER]


class HelpWindow(QWidget):
    """A modeless, always-on-top window that renders a Markdown doc.

    Used for both the in-app Help (help.md, with the auto-generated
    shortcuts table) and the Tutorial (tutorial.md, no shortcuts table).
    Each (filename) gets its own singleton instance so Help and Tutorial
    can be open simultaneously.
    """

    _instances: dict[str, "HelpWindow"] = {}

    @classmethod
    def show_help(cls, parent=None, chapter: str | None = None) -> None:
        """Open (or raise) the Help window, optionally at *chapter*."""
        cls._show_doc("help.md", "Diagrammer Help",
                      show_shortcuts=True, parent=parent, chapter=chapter)

    @classmethod
    def show_tutorial(cls, parent=None) -> None:
        """Open (or raise) the Tutorial window."""
        cls._show_doc("tutorial.md", "Diagrammer Tutorial",
                      show_shortcuts=False, parent=parent)

    @classmethod
    def _show_doc(cls, filename: str, title: str,
                  show_shortcuts: bool, parent=None,
                  chapter: str | None = None) -> None:
        inst = cls._instances.get(filename)
        if inst is None or not inst.isVisible():
            inst = cls(parent, filename=filename, title=title,
                       show_shortcuts=show_shortcuts)
            cls._instances[filename] = inst
        inst.show()
        inst.raise_()
        if chapter:
            inst.go_to_chapter(chapter)
        # Note: do NOT call activateWindow() — we want the editor to keep
        # keyboard focus so users can follow along while reading.

    def __init__(self, parent=None, *, filename: str = "help.md",
                 title: str = "Diagrammer Help",
                 show_shortcuts: bool = True):
        # Tool-window flag keeps it from stealing focus on macOS while
        # WindowStaysOnTopHint keeps it visible above the editor.
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._doc_filename = filename
        self._show_shortcuts = show_shortcuts
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(title)
        self.resize(860, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Search bar
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_label = QLabel("Search:")
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type to search...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        # Return finds the next match, Shift+Return the previous one, as in
        # a browser's find bar. An event filter, not returnPressed or a
        # QShortcut: QLineEdit takes Return whatever the modifiers, so a
        # Shift+Return shortcut never fires.
        self._search.installEventFilter(self)
        self._prev_btn = QPushButton("Prev")
        self._next_btn = QPushButton("Next")
        self._prev_btn.setFixedWidth(50)
        self._next_btn.setFixedWidth(50)
        self._prev_btn.clicked.connect(self._search_prev)
        self._next_btn.clicked.connect(self._search_next)
        self._match_label = QLabel("")
        search_row.addWidget(search_label)
        search_row.addWidget(self._search, 1)
        search_row.addWidget(self._prev_btn)
        search_row.addWidget(self._next_btn)
        search_row.addWidget(self._match_label)
        layout.addLayout(search_row)

        self._browser = QTextBrowser()
        # We handle navigation ourselves so .md links open in a HelpWindow
        # (rendered) instead of being loaded as raw text by QTextBrowser.
        self._browser.setOpenLinks(False)
        self._browser.setOpenExternalLinks(False)
        self._browser.setReadOnly(True)
        self._browser.anchorClicked.connect(self._on_anchor_clicked)
        self._browser.verticalScrollBar().valueChanged.connect(
            self._sync_chapter_to_scroll)

        # Chapter sidebar: the doc's ## headings.
        self._chapter_list = QListWidget()
        self._chapter_list.setWordWrap(True)
        self._chapter_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chapter_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._chapter_list.itemClicked.connect(
            lambda item: self._scroll_to_chapter(self._chapter_list.row(item)))
        self._chapter_list.currentRowChanged.connect(self._on_chapter_row)
        self._chapter_list.setStyleSheet(
            "QListWidget::item { padding: 5px 6px; }")

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._chapter_list)
        splitter.addWidget(self._browser)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([210, 650])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        self._chapter_blocks: list = []   # QTextBlock of each ## heading
        self._syncing = False
        self._matches = 0

        self._load_help()
        self._build_chapters()

    # ------------------------------------------------------------------
    # Chapters
    # ------------------------------------------------------------------

    def chapters(self) -> list[str]:
        return [b.text().strip() for b in self._chapter_blocks]

    def _build_chapters(self) -> None:
        """Read the chapters from the rendered document, so the list is
        exactly what is on the page, generated shortcuts table included."""
        self._chapter_blocks = []
        block = self._browser.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() == 2:
                self._chapter_blocks.append(block)
            block = block.next()
        self._chapter_list.clear()
        for b in self._chapter_blocks:
            QListWidgetItem(b.text().strip(), self._chapter_list)
        # A doc with one chapter or none has nothing to navigate.
        self._chapter_list.setVisible(len(self._chapter_blocks) > 1)
        if self._chapter_blocks:
            self._syncing = True
            self._chapter_list.setCurrentRow(0)
            self._syncing = False

    def go_to_chapter(self, title: str) -> bool:
        """Scroll to the chapter called *title* (case-insensitive)."""
        wanted = title.strip().lower()
        for i, name in enumerate(self.chapters()):
            if name.lower() == wanted:
                self._scroll_to_chapter(i)
                return True
        return False

    def _on_chapter_row(self, row: int) -> None:
        # Keyboard navigation in the sidebar; clicks go through itemClicked.
        if not self._syncing and row >= 0:
            self._scroll_to_chapter(row)

    def _heading_top(self, i: int) -> float:
        layout = self._browser.document().documentLayout()
        return layout.blockBoundingRect(self._chapter_blocks[i]).top()

    def _scroll_to_chapter(self, i: int) -> None:
        if not 0 <= i < len(self._chapter_blocks):
            return
        # Put the cursor at the heading too, so Next continues from here.
        cursor = QTextCursor(self._chapter_blocks[i])
        self._browser.setTextCursor(cursor)
        bar = self._browser.verticalScrollBar()
        bar.setValue(min(int(self._heading_top(i)), bar.maximum()))
        self._set_current_chapter(i)

    def _sync_chapter_to_scroll(self, value: int) -> None:
        """Highlight the chapter being read: the last heading at or above
        the top of the view (or the last chapter, at the very bottom)."""
        if not self._chapter_blocks:
            return
        bar = self._browser.verticalScrollBar()
        if value >= bar.maximum() > 0:
            current = len(self._chapter_blocks) - 1
            # Unless that chapter's heading is still below the view: then the
            # reader is at the end of an earlier, longer chapter.
            while current > 0 and self._heading_top(current) > value + bar.pageStep():
                current -= 1
        else:
            current = 0
            for i in range(len(self._chapter_blocks)):
                if self._heading_top(i) <= value + 8:
                    current = i
        self._set_current_chapter(current)

    def _set_current_chapter(self, i: int) -> None:
        if self._chapter_list.currentRow() != i:
            self._syncing = True
            self._chapter_list.setCurrentRow(i)
            self._syncing = False

    def _mark_chapters(self, positions: list[int], searching: bool) -> None:
        """While searching, dim the chapters that have no match."""
        starts = [b.position() for b in self._chapter_blocks]
        for i in range(self._chapter_list.count()):
            item = self._chapter_list.item(i)
            if not searching:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
                item.setToolTip("")
                continue
            end = starts[i + 1] if i + 1 < len(starts) else float("inf")
            n = sum(1 for p in positions if starts[i] <= p < end)
            item.setData(Qt.ItemDataRole.ForegroundRole, None if n else _DIM)
            item.setToolTip(f"{n} match" + ("" if n == 1 else "es"))

    def eventFilter(self, obj, event) -> bool:
        if (obj is self._search and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._search_prev()
            else:
                self._search_next()
            return True
        return super().eventFilter(obj, event)

    def _on_search(self, text: str) -> None:
        """Highlight every match and jump to the first one in the document.

        Highlights are extra selections, which sit on top of the document
        without editing its formatting, so clearing them can never strip a
        background the doc itself set.
        """
        self._highlight_all(text)
        if not text:
            self._match_label.setText("")
            return
        # Search from the top: wherever the cursor was left (often the end of
        # the document) must not decide whether a term is "found".
        cursor = self._browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._browser.setTextCursor(cursor)
        if self._browser.find(text):
            self._place_match()
        self._update_match_label()

    def _place_match(self) -> None:
        """Scroll so the selected match sits a third of the way down the view.

        find() only makes the match visible, which often leaves it on the
        bottom line with none of the text after it showing.
        """
        rect = self._browser.cursorRect()
        bar = self._browser.verticalScrollBar()
        target = bar.value() + rect.top() - self._browser.viewport().height() // 3
        bar.setValue(max(0, min(target, bar.maximum())))

    def _highlight_all(self, text: str) -> None:
        selections, positions = [], []
        if text:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(255, 230, 120))
            doc = self._browser.document()
            cursor = doc.find(text)
            while not cursor.isNull():
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                sel.format = fmt
                selections.append(sel)
                positions.append(cursor.selectionStart())
                cursor = doc.find(text, cursor)
        self._matches = len(selections)
        self._browser.setExtraSelections(selections)
        self._mark_chapters(positions, bool(text))

    def _update_match_label(self) -> None:
        n = self._matches
        if n == 0:
            self._match_label.setText("No matches")
        else:
            self._match_label.setText(f"{n} match" + ("" if n == 1 else "es"))

    def _search_next(self) -> None:
        self._step(QTextDocument.FindFlag(0), QTextCursor.MoveOperation.Start)

    def _search_prev(self) -> None:
        self._step(QTextDocument.FindFlag.FindBackward,
                   QTextCursor.MoveOperation.End)

    def _step(self, flags, wrap_to) -> None:
        """Move to the next/previous match, wrapping at the document's ends."""
        text = self._search.text()
        if not text:
            return
        found = self._browser.find(text, flags)
        if not found:
            cursor = self._browser.textCursor()
            cursor.movePosition(wrap_to)
            self._browser.setTextCursor(cursor)
            found = self._browser.find(text, flags)
        if found:
            self._place_match()
        self._update_match_label()

    def _on_anchor_clicked(self, url: QUrl) -> None:
        """Route link clicks: render known .md docs in a HelpWindow,
        send everything else to the system browser."""
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        target = url.toString()
        if target.endswith("help.md"):
            HelpWindow.show_help(self.parent())
            return
        if target.endswith("tutorial.md"):
            HelpWindow.show_tutorial(self.parent())
            return
        # Unknown local link — hand off to the OS so users at least see something
        QDesktopServices.openUrl(url)

    def _load_help(self) -> None:
        """Load and render the doc markdown file."""
        help_path = self._find_help_file()
        if help_path is None:
            self._browser.setPlainText(f"Document not found: {self._doc_filename}")
            return

        # Let QTextBrowser resolve relative image paths (e.g. ![](image-1.png))
        # against the doc's directory.
        self._browser.setSearchPaths([str(help_path.parent)])

        md_text = help_path.read_text(encoding="utf-8")

        # Convert markdown to HTML — try markdown lib, fall back to basic rendering
        try:
            import markdown
            html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
        except ImportError:
            html = self._basic_md_to_html(md_text)

        # Cap image width so high-res screenshots fit the window. Qt's text
        # engine preserves aspect ratio when only width is set. We skip imgs
        # that already specify a width.
        import re
        html = re.sub(
            r'<img(?![^>]*\bwidth=)',
            '<img width="560"',
            html,
        )

        # Add some basic styling. The help window is rendered as a
        # "paper" document with an explicit white background and black
        # text regardless of the chrome theme — otherwise the hard-coded
        # light-gray element backgrounds (table headers, code blocks)
        # become invisible under a dark-mode palette.
        styled = f"""
        <html><head><style>
            body {{ font-family: Helvetica, Arial, sans-serif;
                   font-size: 13px; padding: 20px; line-height: 1.5;
                   background: #ffffff; color: #000000; }}
            h1 {{ font-size: 20px; color: #000000;
                  border-bottom: 1px solid #ddd; padding-bottom: 6px; }}
            h2 {{ font-size: 16px; margin-top: 20px; color: #000000;
                  border-bottom: 1px solid #eee; padding-bottom: 4px; }}
            h3 {{ font-size: 14px; margin-top: 16px; color: #000000; }}
            h4 {{ font-size: 13px; margin-top: 12px; color: #000000; }}
            p, li {{ color: #000000; }}
            table {{ border-collapse: collapse; margin: 8px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 4px 10px;
                      text-align: left; color: #000000; }}
            th {{ background: #f5f5f5; color: #000000; }}
            code {{ background: #f0f0f0; color: #000000; padding: 1px 4px;
                    border-radius: 3px; font-size: 12px; }}
            pre {{ background: #f0f0f0; color: #000000; padding: 10px;
                   border-radius: 4px; overflow-x: auto; }}
            hr {{ border: none; border-top: 1px solid #ddd; margin: 16px 0; }}
            kbd {{ background: #f0f0f0; color: #000000;
                   border: 1px solid #ccc; border-radius: 3px;
                   padding: 1px 5px; font-size: 12px; font-family: monospace; }}
        </style></head><body>
        {html}
        </body></html>
        """
        # Append auto-generated keyboard shortcuts section (help.md only)
        if self._show_shortcuts:
            shortcuts_html = self._generate_shortcuts_html()
            styled = styled.replace("</body>", f"{shortcuts_html}</body>")

        self._browser.setHtml(styled)

    @staticmethod
    def _generate_shortcuts_html() -> str:
        """Generate an HTML keyboard shortcuts table from the shortcut registry."""
        from diagrammer.shortcuts import shortcuts_by_category
        html = "<hr><h2>Keyboard Shortcuts</h2>\n"
        html += (
            "<p style='color:#666; font-size:12px;'>"
            "These reflect your current shortcuts. Defaults can be customized "
            "in <i>Settings → Keyboard Shortcuts</i>. "
            "Customized entries are marked <i>(custom)</i>."
            "</p>\n"
        )
        for cat, shortcuts in shortcuts_by_category().items():
            html += f"<h3>{cat}</h3>\n"
            html += "<table><tr><th>Shortcut</th><th>Action</th></tr>\n"
            for s in shortcuts:
                key = s.display_text
                if not key:
                    continue
                marker = " <i style='color:#888;'>(custom)</i>" if s.is_overridden else ""
                html += (
                    f"<tr><td><kbd>{key}</kbd></td>"
                    f"<td>{s.description}{marker}</td></tr>\n"
                )
            html += "</table>\n"
        return html

    def _find_help_file(self) -> Path | None:
        return find_doc(self._doc_filename)

    @staticmethod
    def _basic_md_to_html(md: str) -> str:
        """Very basic markdown→HTML fallback (no external deps)."""
        import re
        lines = md.split("\n")
        html_lines = []
        in_table = False
        in_code = False
        in_list = False

        def _inline(text: str) -> str:
            """Apply inline formatting: bold, inline code."""
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
            text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
            return text

        for line in lines:
            # Fenced code blocks
            if line.strip().startswith("```"):
                if in_code:
                    html_lines.append("</pre>")
                    in_code = False
                else:
                    html_lines.append("<pre>")
                    in_code = True
                continue
            if in_code:
                html_lines.append(line)
                continue

            # Headings
            if line.startswith("### "):
                html_lines.append(f"<h3>{_inline(line[4:])}</h3>")
                continue
            if line.startswith("## "):
                html_lines.append(f"<h2>{_inline(line[3:])}</h2>")
                continue
            if line.startswith("# "):
                html_lines.append(f"<h1>{_inline(line[2:])}</h1>")
                continue

            # Horizontal rule
            if line.strip() == "---":
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                html_lines.append("<hr>")
                continue

            # Tables
            if "|" in line:
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if all(set(c) <= {"-", ":", " "} for c in cells):
                    continue  # separator row
                tag = "th" if not in_table else "td"
                if not in_table:
                    html_lines.append("<table>")
                    in_table = True
                    tag = "th"
                row = "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells)
                html_lines.append(f"<tr>{row}</tr>")
                continue
            elif in_table:
                html_lines.append("</table>")
                in_table = False

            line = _inline(line)

            # Numbered list items
            m = re.match(r'^(\d+)\.\s+(.*)', line)
            if m:
                if not in_list:
                    html_lines.append("<ol>")
                    in_list = "ol"
                html_lines.append(f"<li>{m.group(2)}</li>")
                continue

            # Unordered list items
            if line.startswith("- "):
                if not in_list:
                    html_lines.append("<ul>")
                    in_list = "ul"
                html_lines.append(f"<li>{line[2:]}</li>")
                continue

            # Close list if we're no longer in one
            if in_list and line.strip():
                html_lines.append(f"</{in_list}>")
                in_list = False

            # Paragraph
            if line.strip():
                html_lines.append(f"<p>{line}</p>")
            else:
                if in_list:
                    html_lines.append(f"</{in_list}>")
                    in_list = False
                html_lines.append("")

        if in_table:
            html_lines.append("</table>")
        if in_list:
            html_lines.append(f"</{in_list}>")

        return "\n".join(html_lines)
