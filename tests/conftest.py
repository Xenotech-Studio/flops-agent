"""Standalone test configuration.

The suite deliberately does not add the Flops monorepo's ``backend/`` directory
to ``sys.path``. Install this checkout first (for example,
``pip install -e .[test,providers]``) so tests exercise the distributable
package boundary.
"""

import flops_agent


def pytest_sessionstart() -> None:
    """Fail early if the independently installed package cannot be imported."""
    assert flops_agent.__version__
