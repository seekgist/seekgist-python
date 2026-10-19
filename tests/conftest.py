import sys
from pathlib import Path

# The tests import their helpers (fakes.py) as a top-level module.
sys.path.insert(0, str(Path(__file__).parent))
