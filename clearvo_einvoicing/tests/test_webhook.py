import hashlib
import hmac
import json
import time

from odoo.tests import tagged
from odoo.tests.common import HttpCase

_TEST_SECRET = 'whsec_test_clearvo_webhook_secret_fixture'


@tagged('post_install', '-at_install', 'clearvo')
class TestClearvoWebhookController(HttpCase):
    """
    Tests for the /clearvo/webhook/<company_id> HTTP endpoint.
    HttpCase gives us a real HTTP client to POST against the controller.
    """

    def setUp(self):
        super().setUp()
        company = self.env.company
        company.clearvo_webhook_secret = _TEST_SECRET
        self.company_id = company.id

        self.invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.env['res.partner'].search([('vat', '!=', False)], limit=1).id
            or self.env['res.partner'].create({'name': 'Webhook Test Partner'}).id,
            'clearvo_ref_id': 'ref-webhook-test-001',
            'clearvo_status': 'pending',
        })

    def _sign(self, body: bytes, secret: str = _TEST_SECRET, ts: int = None):
        if ts is None:
            ts = int(time.time())
        message = f'{ts}.{body.decode("utf-8")}'
        sig = 'sha256=' + hmac.new(
            secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return ts, sig

    def _post_webhook(self, payload, secret=_TEST_SECRET, ts=None, corrupt_sig=False):
        body = json.dumps(payload).encode()
        ts, sig = self._sign(body, secret=secret, ts=ts)
        if corrupt_sig:
            sig = sig[:-4] + 'xxxx'
        return self.url_open(
            f'/clearvo/webhook/{self.company_id}',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'x-taxually-timestamp': str(ts),
                'x-taxually-signature': sig,
            },
        )

    def test_webhook_accepted_updates_invoice(self):
        resp = self._post_webhook({
            'referenceId': 'ref-webhook-test-001',
            'clearanceStatus': 'ACCEPTED',
        })
        self.assertEqual(resp.status_code, 200)
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice.clearvo_status, 'accepted')

    def test_webhook_rejected_updates_invoice(self):
        resp = self._post_webhook({
            'referenceId': 'ref-webhook-test-001',
            'clearanceStatus': 'REJECTED',
            'rejectionReason': 'Buyer VAT not found in authority registry.',
        })
        self.assertEqual(resp.status_code, 200)
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice.clearvo_status, 'rejected')
        self.assertIn('VAT not found', self.invoice.clearvo_error)

    def test_webhook_invalid_signature_rejected(self):
        resp = self._post_webhook({
            'referenceId': 'ref-webhook-test-001',
            'clearanceStatus': 'ACCEPTED',
        }, corrupt_sig=True)
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.json().get('ok'))

    def test_webhook_missing_secret_returns_503(self):
        self.env.company.clearvo_webhook_secret = False
        resp = self._post_webhook({
            'referenceId': 'ref-webhook-test-001',
            'clearanceStatus': 'ACCEPTED',
        })
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json().get('ok'))
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice.clearvo_status, 'pending')

    def test_webhook_expired_timestamp_rejected(self):
        stale_ts = int(time.time()) - 400
        resp = self._post_webhook({
            'referenceId': 'ref-webhook-test-001',
            'clearanceStatus': 'ACCEPTED',
        }, ts=stale_ts)
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.json().get('ok'))

    def test_webhook_missing_headers_rejected(self):
        body = json.dumps({'referenceId': 'ref-webhook-test-001', 'clearanceStatus': 'ACCEPTED'}).encode()
        resp = self.url_open(
            f'/clearvo/webhook/{self.company_id}',
            data=body,
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(resp.status_code, 401)

    def test_webhook_invalid_json_returns_400(self):
        body = b'not json at all'
        ts, sig = self._sign(body)
        resp = self.url_open(
            f'/clearvo/webhook/{self.company_id}',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'x-taxually-timestamp': str(ts),
                'x-taxually-signature': sig,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get('ok'))

    def test_webhook_unknown_ref_returns_ok_true(self):
        resp = self._post_webhook({
            'referenceId': 'ref-does-not-exist-xyz',
            'clearanceStatus': 'ACCEPTED',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get('ok'))

    def test_webhook_unknown_company_returns_404(self):
        body = json.dumps({'referenceId': 'ref-webhook-test-001', 'clearanceStatus': 'ACCEPTED'}).encode()
        ts, sig = self._sign(body)
        resp = self.url_open(
            '/clearvo/webhook/999999',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'x-taxually-timestamp': str(ts),
                'x-taxually-signature': sig,
            },
        )
        self.assertEqual(resp.status_code, 404)
