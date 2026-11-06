"""The verifier: typed extraction (Jev) -> counterfactual replay (decides) -> claim checks (explain)."""
from .verdict import Verdict, verify

__all__ = ["Verdict", "verify"]
