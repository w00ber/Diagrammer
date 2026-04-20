"""HelpWindow — modeless window displaying help.md as rendered HTML."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class HelpWindow(QWidget):
    """A modeless, always-on-top window that renders a Markdown doc.

    Used for both the in-app Help (help.md, with the auto-generated
    shortcuts table) and the Tutorial (tutorial.md, no shortcuts table).
    Each (filename) gets its own singleton instance so Help and Tutorial
    can be open simultaneously.
    """

    _instances: dict[str, "HelpWindow"] = {}

    @classmethod
    def show_help(cls, parent=None) -> None:
        """Open (or raise) the Help window with keyboard shortcuts table."""
        cls._show_doc("help.md", "Diagrammer Help",
                      show_shortcuts=True, parent=parent)

    @classmethod
    def show_tutorial(cls, parent=None) -> None:
        """Open (or raise) the Tutorial window."""
        cls._show_doc("tutorial.md", "Diagrammer Tutorial",
                      show_shortcuts=False, parent=parent)

    @classmethod
    def _show_doc(cls, filename: str, title: str,
                  show_shortcuts: bool, parent=None) -> None:
        inst = cls._instances.get(filename)
        if inst is None or not inst.isVisible():
            inst = cls(parent, filename=filename, title=title,
                       show_shortcuts=show_shortcuts)
            cls._instances[filename] = inst
        inst.show()
        inst.raise_()
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
        self.resize(650, 700)

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
        self._browser.setOpenExternalLinks(True)
        self._browser.setReadOnly(True)
        layout.addWidget(self._browser)

        self._load_help()

    def _on_search(self, text: str) -> None:
        """Highlight all matches and jump to the first one."""
        # Clear previous highlights
        cursor = self._browser.textCursor()
        cursor.select(cursor.SelectionType.Document)
        fmt = cursor.charFormat()
        fmt.setBackground(Qt.GlobalColor.transparent)
        cursor.mergeCharFormat(fmt)
        cursor.clearSelection()
        self._browser.setTextCursor(cursor)

        if not text:
            self._match_label.setText("")
            return

        # Find and jump to first match
        found = self._browser.find(text)
        if found:
            self._match_label.setText("")
        else:
            self._match_label.setText("No matches")

    def _search_next(self) -> None:
        text = self._search.text()
        if text:
            if not self._browser.find(text):
                # Wrap around to start
                cursor = self._browser.textCursor()
                cursor.movePosition(cursor.MoveOperation.Start)
                self._browser.setTextCursor(cursor)
                self._browser.find(text)

    def _search_prev(self) -> None:
        text = self._search.text()
        if text:
            if not self._browser.find(text, QTextDocument.FindFlag.FindBackward):
                # Wrap around to end
                cursor = self._browser.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self._browser.setTextCursor(cursor)
                self._browser.find(text, QTextDocument.FindFlag.FindBackward)

    def _load_help(self) -> None:
        """Load and render the doc markdown file."""
        help_path = self._find_help_file()
        if help_path is None:
            self._browser.setPlainText(f"Document not found: {self._doc_filename}")
            return

        md_text = help_path.read_text(encoding="utf-8")

        # Convert markdown to HTML — try markdown lib, fall back to basic rendering
        try:
            import markdown
            html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
        except ImportError:
            html = self._basic_md_to_html(md_text)

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
        """Locate the doc file in the package's docs/ directory, with a
        fall-back to the repo-root docs/ folder for development checkouts."""
        here = Path(__file__).resolve().parent
        candidates = [
            here.parent / "docs" / self._doc_filename,
            here.parent.parent.parent / "docs" / self._doc_filename,
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

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
