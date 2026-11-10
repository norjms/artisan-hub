import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_core import env  # noqa: E402,F401  (session fixture shared by all test modules)
