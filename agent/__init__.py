"""IntentQL as a LangGraph agent.

    router ─┬─ data ────────── pipeline (compiler) ─┬─ ok ──────────────── remember → END
            │                                        ├─ needs_clarification → clarify ↺ (≤ 2)
            │                                        └─ out of scope ─────── knowledge path
            ├─ knowledge ───── retrieve → generate
            ├─ explain ─────── retrieve → explain (the last result)
            └─ investigate ─── find_entities → retrieve(playbook) → plan → execute_step ↺ (≤ 5)
                               → synthesize

The compiler pipeline (app/pipeline.py) is used as a tool, unchanged. See agent/graph.py
for the wiring, agent/nodes.py for what each node does, agent/server.py for the HTTP API.
"""
