"""python -m gtpfit.app [--port N] — start the workbench."""
import sys
from pathlib import Path


def main():
    from streamlit.web import cli
    port = "8501"
    if "--port" in sys.argv:
        port = sys.argv[sys.argv.index("--port") + 1]
    app = Path(__file__).with_name("workbench.py")
    sys.argv = ["streamlit", "run", str(app), "--server.port", port, "--server.headless", "true",
                "--server.enableStaticServing", "true",  # app/static/: the HAI loader and three.js
                "--browser.gatherUsageStats", "false"]
    sys.exit(cli.main())


main()
