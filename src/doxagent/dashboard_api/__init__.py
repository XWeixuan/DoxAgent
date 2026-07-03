"""Dashboard State API application package."""

from doxagent.dashboard_api.app import create_app
from doxagent.dashboard_api.mock_router import DASHBOARD_API_PREFIX, create_mock_router
from doxagent.dashboard_api.real_router import create_real_router
from doxagent.dashboard_api.real_service import RealDashboardOverviewService

__all__ = [
    "DASHBOARD_API_PREFIX",
    "RealDashboardOverviewService",
    "create_app",
    "create_mock_router",
    "create_real_router",
]
