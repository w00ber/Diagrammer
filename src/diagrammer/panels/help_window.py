"""HelpWindow — modeless window displaying help.md as rendered HTML."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget


class HelpWindow(QWidget):
    """A modeless, always-on-top window that renders help.md."""

    _instance: HelpWindow | None = None

    @classmethod
    def show_help(cls, parent=None) -> None:
        """Show the help window (singleton — only one instance)."""
        if cls._instance is None or not cls._instance.isVisible():
            cls._instance = cls(parent)
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Diagrammer Help")
        self.resize(650, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setReadOnly(True)
        layout.addWidget(self._browser)

        self._load_help()

    def _load_help(self) -> None:
        """Load and render help.md."""
        # Find help.md relative to the package
        help_path = self._find_help_file()
        if help_path is None:
            self._browser.setPlainText("Help file not found.")
            return

        md_text = help_path.read_text(encoding="utf-8")

        # Convert markdown to HTML — try markdown lib, fall back to basic rendering
        try:
            import markdown
            html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
        except ImportError:
            html = self._basic_md_to_html(md_text)

        # Add some basic styling
        styled = f"""
        <html><head><style>
            body {{ font-family: Helvetica, Arial, sans-serif;
                   font-size: 13px; padding: 16px; line-height: 1.5; }}
            h1 {{ font-size: 20px; border-bottom: 1px solid #ddd; padding-bottom: 6px; }}
            h2 {{ font-size: 16px; margin-top: 20px; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
            h3 {{ font-size: 14px; margin-top: 16px; }}
            table {{ border-collapse: collapse; margin: 8px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 4px 10px; text-align: left; }}
            th {{ background: #f5f5f5; }}
            code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 12px; }}
            pre {{ background: #f0f0f0; padding: 10px; border-radius: 4px; overflow-x: auto; }}
            hr {{ border: none; border-top: 1px solid #ddd; margin: 16px 0; }}
            kbd {{ background: #f0f0f0; border: 1px solid #ccc; border-radius: 3px;
                   padding: 1px 5px; font-size: 12px; font-family: monospace; }}
        </style></head><body>
        {html}
        </body></html>
        """
        # Append auto-generated keyboard shortcuts section
        shortcuts_html = self._generate_shortcuts_html()
        styled = styled.replace("</body>", f"{shortcuts_html}</body>")

        self._browser.setHtml(styled)

    @staticmethod
    def _generate_shortcuts_html() -> str:
        """Generate an HTML keyboard shortcuts table from the shortcut registry."""
        from diagrammer.shortcuts import shortcuts_by_category
        html = "<hr><h2>Keyboard Shortcuts</h2>\n"
        for cat, shortcuts in shortcuts_by_category().items():
            html += f"<h3>{cat}</h3>\n"
            html += "<table><tr><th>Shortcut</th><th>Action</th></tr>\n"
            for s in shortcuts:
                key = s.display_text
                if not key:
                    continue
                html += f"<tr><td><kbd>{key}</kbd></td><td>{s.description}</td></tr>\n"
            html += "</table>\n"
        return html

    @staticmethod
    def _find_help_file() -> Path | None:
        """Locate help.md in the docs directory."""
        here = Path(__file__).resolve().parent
        for ancestor in [here.parent.parent, here.parent.parent.parent]:
            candidate = ancestor / "docs" / "help.md"
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _basic_md_to_html(md: str) -> str:
        """Very basic markdown→HTML fallback (no external deps)."""
        import re
        lines = md.split("\n")
        html_lines = []
        in_table = False
        in_code = False

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
                html_lines.append(f"<h3>{line[4:]}</h3>")
                continue
            if line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
                continue
            if line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
                continue

            # Horizontal rule
            if line.strip() == "---":
                html_lines.append("<hr>")
                continue

            # Tables
            if "|" in line:
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if all(set(c) <= {"-", ":", " "} for c in cells):
                    continue  # separator row
                tag = "th" if not in_table else "td"
                if not in_table:
                    html_lines.append("<table>")
                    in_table = True
                    tag = "th"
                row = "".join(f"<{tag}>{c}</{tag}>" for c in cells)
                html_lines.append(f"<tr>{row}</tr>")
                continue
            elif in_table:
                html_lines.append("</table>")
                in_table = False

            # Bold / inline code
            line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            line = re.sub(r'`(.+?)`', r'<code>\1</code>', line)

            # List items
            if line.startswith("- "):
                html_lines.append(f"<li>{line[2:]}</li>")
                continue

            # Paragraph
            if line.strip():
                html_lines.append(f"<p>{line}</p>")
            else:
                html_lines.append("")

        if in_table:
            html_lines.append("</table>")

        return "\n".join(html_lines)
