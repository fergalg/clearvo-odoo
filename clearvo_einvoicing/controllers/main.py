import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_SIGNATURE_TOLERANCE_SECONDS = 300  # 5-minute replay window


class ClearvoWebhookController(http.Controller):

    @http.route('/clearvo/webhook', type='http', auth='none', csrf=False, methods=['POST'])
    def clearvo_webhook(self):
        """
        Receive clearance status push notifications from Clearvo.

        Signature verification: Clearvo signs each delivery as
            sha256=HMAC-SHA256(secret, f"{timestamp}.{body}")
        where secret = the whsec_... value stored on the company at connect time.
        We reject deliveries with an invalid signature or a timestamp older than 5 minutes.
        If no secret is stored (webhook registered before this version), we fall back to
        reference-ID validation only (same behaviour as before).
        """
        raw_body = request.httprequest.get_data()

        # ── Signature verification ────────────────────────────────────────────
        sig_header = request.httprequest.headers.get('x-taxually-signature', '')
        ts_header = request.httprequest.headers.get('x-taxually-timestamp', '')

        company = request.env['res.company'].sudo().search([], limit=1)
        webhook_secret = company.clearvo_webhook_secret if company else None

        if webhook_secret:
            if not sig_header or not ts_header:
                _logger.warning('Clearvo webhook: missing signature or timestamp headers')
                return request.make_response(
                    json.dumps({'ok': False, 'error': 'missing_signature'}),
                    headers=[('Content-Type', 'application/json')],
                    status=401,
                )

            try:
                timestamp = int(ts_header)
            except ValueError:
                return request.make_response(
                    json.dumps({'ok': False, 'error': 'invalid_timestamp'}),
                    headers=[('Content-Type', 'application/json')],
                    status=400,
                )

            if abs(time.time() - timestamp) > _SIGNATURE_TOLERANCE_SECONDS:
                _logger.warning('Clearvo webhook: timestamp too old or too far in the future')
                return request.make_response(
                    json.dumps({'ok': False, 'error': 'timestamp_expired'}),
                    headers=[('Content-Type', 'application/json')],
                    status=401,
                )

            message = f'{timestamp}.{raw_body.decode("utf-8")}'
            expected = 'sha256=' + hmac.new(
                webhook_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(expected, sig_header):
                _logger.warning('Clearvo webhook: signature mismatch')
                return request.make_response(
                    json.dumps({'ok': False, 'error': 'invalid_signature'}),
                    headers=[('Content-Type', 'application/json')],
                    status=401,
                )

        # ── Parse and dispatch ────────────────────────────────────────────────
        try:
            payload = json.loads(raw_body)
        except (ValueError, TypeError):
            _logger.warning('Clearvo webhook: invalid JSON body')
            return request.make_response(
                json.dumps({'ok': False, 'error': 'invalid_json'}),
                headers=[('Content-Type', 'application/json')],
                status=400,
            )

        if not isinstance(payload, dict):
            return request.make_response(
                json.dumps({'ok': False, 'error': 'invalid_payload'}),
                headers=[('Content-Type', 'application/json')],
                status=400,
            )

        ok = request.env['account.move'].sudo().clearvo_handle_webhook(payload)
        return request.make_response(
            json.dumps({'ok': ok}),
            headers=[('Content-Type', 'application/json')],
        )
