"""HTTP routers.

Routers stay thin: validate input, resolve ownership, delegate to a service,
and let errors propagate to the handlers in ``app.main``.
"""

from . import auth, evaluation, farmer, health, market, outcomes, recommendation, soil, weather

__all__ = [
    "auth",
    "evaluation",
    "farmer",
    "health",
    "market",
    "outcomes",
    "recommendation",
    "soil",
    "weather",
]
