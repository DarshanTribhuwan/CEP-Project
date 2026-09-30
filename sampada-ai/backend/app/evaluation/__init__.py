"""Evaluation: scoring models against outcomes they could not have seen.

Part 4 of the four-part split. The design conversation requires that a
candidate model never replace the production one without beating it on
held-out data; this package is where that comparison is computed rather than
asserted.
"""
