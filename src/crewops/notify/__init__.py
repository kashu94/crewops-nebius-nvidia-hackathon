"""Telling people what changed: live console updates and optional email alerts.

Both are driven by the table's change stream, so they follow what was actually stored,
whoever stored it. Neither is ever an input: nothing reads them back.
"""
