import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase


@tagged('post_install', '-at_install', 'clearvo')
class TestClearvoWebhookController(HttpCase):
    """
    Tests for the /clearvo/webhook HTTP endpoint.
    HttpCase gives us a real HTTP client to POST against the controller.
    """

    def setUp(self):
        super().setUp()
        self.invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.env['res.partner'].search([('vat', '!=', False)], limit=1).id
            or self.env['res.partner'].create({'name': 'Webhook Test Partner'}).id,
            'clearvo_ref_id': 'ref-webhook-test-001',
            'clearvo_status': 'pending',
        })

    def _post_webhook(self, payload):
        return self.url_open(
            '/clearvo/webhook',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
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

    def test_webhook_invalid_json_returns_200_with_error(self):
        resp = self.url_open(
            '/clearvo/webhook',
            data=b'not json at all',
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body.get('ok'))

    def test_webhook_unknown_ref_returns_ok_true(self):
        resp = self._post_webhook({
            'referenceId': 'ref-does-not-exist-xyz',
            'clearanceStatus': 'ACCEPTED',
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get('ok'))
