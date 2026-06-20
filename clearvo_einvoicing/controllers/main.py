import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_SIGNATURE_TOLERANCE_SECONDS = 300  # 5-minute replay window

# Statuses that tax authorities report back via Clearvo. Only these values are
# accepted — the webhook relays authority decisions, not user input.
_AUTHORITY_STATUSES = frozenset({'ACCEPTED', 'REJECTED', 'PENDING', 'FAILED'})


class ClearvoWebhookController(http.Controller):

    @http.route('/clearvo/webhook/<int:company_id>', type='http', auth='none', csrf=False, methods=['POST'])
    def clearvo_webhook(self, company_id):
        """
        Receive clearance status push notifications from Clearvo.

        Clearvo acts as a relay — statuses originate from the tax authority
        (SDI, KSeF, ANAF, etc.) and are signed by Clearvo before delivery.
        The HMAC signature proves the delivery came from Clearvo; Clearvo only
        forwards decisions it received from the authority.

        The company ID in the URL identifies which company's webhook secret
        to use for HMAC verification, avoiding multi-company ambiguity.

        Signature: sha256=HMAC-SHA256(secret, f"{timestamp}.{body}")
        Tolerance: 5-minute replay window.
        """
        raw_body = request.httprequest.get_data()

        # ── Resolve company ───────────────────────────────────────────────────
        company = request.env['res.company'].sudo().browse(company_id)
        if not company.exists():
            _logger.warning('Clearvo webhook: unknown company_id %s', company_id)
            return request.make_response(
                json.dumps({'ok': False, 'error': 'unknown_company'}),
                headers=[('Content-Type', 'application/json')],
                status=404,
            )

        # ── Signature verification ────────────────────────────────────────────
        # Always required — without a secret we cannot confirm the delivery
        # originated from Clearvo rather than an attacker forging authority responses.
        webhook_secret = company.clearvo_webhook_secret or None
        if not webhook_secret:
            _logger.warning('Clearvo webhook: company %s has no webhook secret — rejecting delivery', company_id)
            return request.make_response(
                json.dumps({'ok': False, 'error': 'webhook_not_configured'}),
                headers=[('Content-Type', 'application/json')],
                status=503,
            )

        sig_header = request.httprequest.headers.get('x-taxually-signature', '')
        ts_header = request.httprequest.headers.get('x-taxually-timestamp', '')

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

        # ── Parse payload ─────────────────────────────────────────────────────
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

        # ── Validate clearanceStatus ──────────────────────────────────────────
        # Only accept statuses that tax authorities actually report — this is a
        # relay from the authority, not a field an external caller can set freely.
        status = payload.get('clearanceStatus', '')
        if status not in _AUTHORITY_STATUSES:
            _logger.warning('Clearvo webhook: unrecognised clearanceStatus %r', status)
            return request.make_response(
                json.dumps({'ok': False, 'error': 'invalid_status'}),
                headers=[('Content-Type', 'application/json')],
                status=400,
            )

        # ── Dispatch ──────────────────────────────────────────────────────────
        ok = request.env['account.move'].sudo().clearvo_handle_webhook(payload)
        return request.make_response(
            json.dumps({'ok': ok}),
            headers=[('Content-Type', 'application/json')],
        )
