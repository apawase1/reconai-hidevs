import os
import sys

# Make sure `tools/` and friends import as `tools.xxx` regardless of where
# pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
