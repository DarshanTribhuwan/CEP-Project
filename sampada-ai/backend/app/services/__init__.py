"""Decision services (Part 3 of the four-part split).

  crops         - reference knowledge base accessors
  soil          - multi-source soil evidence fusion
  price         - versioned naive price-range baseline
  suitability   - agronomic scoring (soil, water, season, weather)
  community     - app-scoped oversupply signalling
  recommendation- orchestrator: blends factors, explains, records
"""

from . import community, crops, price, recommendation, soil, suitability

__all__ = ["community", "crops", "price", "recommendation", "soil", "suitability"]
