import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ClearvoWebhookController(http.Controller):

    @http.route('/clearvo/webhook', type='json', auth='none', csrf=False, methods=['POST'])
    def clearvo_webhook(self):
        """
        Receive clearance status push notifications from Clearvo.
        Validation: the referenceId in the payload must match an invoice we submitted —
        an attacker would need to enumerate valid reference IDs to spoof a status change,
        which is handled inside clearvo_handle_webhook().
        """
        raw_body = request.httprequest.get_data()

        try:
            payload = json.loads(raw_body)
        except (ValueError, TypeError):
            _logger.warning('Clearvo webhook: invalid JSON body')
            return {'ok': False, 'error': 'invalid_json'}

        if not isinstance(payload, dict):
            return {'ok': False, 'error': 'invalid_payload'}

        ok = request.env['account.move'].sudo().clearvo_handle_webhook(payload)
        return {'ok': ok}
