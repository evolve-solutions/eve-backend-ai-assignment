import sys
from pathlib import Path

# Lets tests import `tests.fixtures` without installing them.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
