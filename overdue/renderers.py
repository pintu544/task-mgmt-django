"""Custom DRF renderer that wraps every response in the standard JSON envelope.

The envelope shape is shared with the Laravel_API and is documented in the
design doc:

    { "success": bool, "data": <payload>|null, "message": str, "errors": <map>|null }

Views in this service typically return one of:

* ``Response(payload)`` — payload becomes ``data``, ``message`` is ``""``.
* ``Response({"data": payload, "message": "..."})`` — explicit message.
* ``Response({"success": False, "data": None, "message": ..., "errors": ...})``
  — fully-constructed envelope (e.g. from the exception handler); pass through.

The renderer keeps the existing JSON serialization behavior of DRF's
``JSONRenderer`` and only adjusts the wrapping step.
"""

from __future__ import annotations

from rest_framework.renderers import JSONRenderer


class EnvelopeRenderer(JSONRenderer):
    """JSONRenderer that wraps response payloads in the project envelope."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        envelope = self._build_envelope(data)
        return super().render(envelope, accepted_media_type, renderer_context)

    @staticmethod
    def _build_envelope(data):
        # No body at all (e.g. logout-style 200 responses).
        if data is None:
            return {
                'success': True,
                'data': None,
                'message': '',
                'errors': None,
            }

        # Already a fully-constructed envelope (typical for the exception
        # handler). Detect it and pass through unchanged.
        if isinstance(data, dict) and 'success' in data and 'data' in data:
            return data

        # View opted into a structured response with explicit ``data`` and/or
        # ``message`` keys.
        if isinstance(data, dict) and ('data' in data or 'message' in data):
            payload = data.get('data')
            message = data.get('message', '') or ''
            return {
                'success': True,
                'data': payload,
                'message': message,
                'errors': None,
            }

        # Plain payload — wrap as-is.
        return {
            'success': True,
            'data': data,
            'message': '',
            'errors': None,
        }
