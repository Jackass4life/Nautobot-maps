"""What the API answers (#449), one module per area: pydantic models that
``/docs`` and ``/openapi.json`` show for each route.

They describe the answers, they don't make them: a route still returns its
own ``deps.Json``, so nothing on the wire changes.  The tests check every
answer they get against its model, so the two can't drift apart.
"""
