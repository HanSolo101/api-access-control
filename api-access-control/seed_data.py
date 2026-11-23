import sys, os

# Ensure project root is in PYTHONPATH
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.app.seed import main

if __name__ == "__main__":
    main()
