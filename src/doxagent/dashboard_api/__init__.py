"""Dashboard State API application package."""

from doxagent.dashboard_api.app import create_app
from doxagent.dashboard_api.mock_router import DASHBOARD_API_PREFIX, create_mock_router

__all__ = ["DASHBOARD_API_PREFIX", "create_app", "create_mock_router"]
