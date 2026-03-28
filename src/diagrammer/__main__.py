"""Entry point for running Diagrammer as `python -m diagrammer`."""

from __future__ import annotations

import sys


def main() -> int:
    from diagrammer.app import create_app

    app, window = create_app()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
