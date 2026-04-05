# Re-export bridge — implementation moved to core/utils/circuit_breaker.py
from core.utils.circuit_breaker import *  # noqa: F401,F403
from core.utils.circuit_breaker import CircuitOpenError, CircuitState, get_breaker  # noqa: F401
