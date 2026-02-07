#!/usr/bin/env python3
"""Run all Flux Torrent unit tests."""
import sys
import os
import unittest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = loader.discover('tests', pattern='test_*.py')
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
