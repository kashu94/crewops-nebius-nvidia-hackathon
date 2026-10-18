"""The question-answering pipeline: route, plan, call tools, verify, render.

Everything here is deterministic. The model calls (triage, advisor, explainer) plug in
through the hooks the pipeline exposes, and their output passes the same verifier.
"""
