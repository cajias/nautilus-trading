"""Shared pytest fixtures for nautilus-trading tests."""
import sys
from pathlib import Path

# Ensure strategies/ at root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))
