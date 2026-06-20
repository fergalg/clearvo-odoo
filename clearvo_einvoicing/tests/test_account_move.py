from unittest.mock import patch, MagicMock

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


@tagged('post_install', '-at_install', 'clearvo')
class TestClearvoAccountMove(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env.company
        cls.company.write({
            'clearvo_api_key': 'csk_test_odootest',
            'clearvo_auto_submit': False,  # manual control in tests
            'vat': 'BE0123456789',
            'country_id': cls.env.ref('base.be').id,
        })

        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Buyer NV',
            'vat': 'BE0987654321',
            'country_id': cls.env.ref('base.be').id,
            'city': 'Brussels',
            'street': 'Rue de la Loi 1',
            'zip': '1000',
        })

        cls.tax_s = cls.env['account.tax'].create({
            'name': '21% VAT',
            'amount': 21.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'clearvo_tax_code': 'S',
        })

        cls.tax_no_code = cls.env['account.tax'].create({
            'name': '6% VAT (unconfigured)',
            'amount': 6.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'clearvo_tax_code': False,
        })

        cls.account = cls.env['account.account'].search([
            ('account_type', 'in', ('income', 'income_other')),
            ('company_id', '=', cls.company.id),
        ], limit=1)

        cls.journal = cls.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', cls.company.id),
        ], limit=1)

    def _make_invoice(self, taxes=None, move_type='out_invoice'):
        if taxes is None:
            taxes = self.tax_s
        return self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.partner.id,
            'journal_id': self.journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Consulting services',
                'quantity': 2.0,
                'price_unit': 500.0,
                'account_id': self.account.id,
                'tax_ids': [(6, 0, taxes.ids)],
            })],
        })

    def _mock_response(self, status_code, json_body):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_body
        mock.text = str(json_body)
        return mock

    # ── Payload building ──────────────────────────────────────────────────────

    def test_payload_has_required_fields(self):
        invoice = self._make_invoice()
        payload = invoice._clearvo_build_payload()
        for field in ('documentType', 'invoiceNumber', 'issueDate', 'currency',
                      'country', 'supplier', 'buyer', 'lines'):
            self.assertIn(field, payload, f'Payload missing field: {field}')

    def test_payload_document_type_invoice(self):
        invoice = self._make_invoice(move_type='out_invoice')
        self.assertEqual(invoice._clearvo_build_payload()['documentType'], 'invoice')

    def test_payload_document_type_credit_note(self):
        credit = self._make_invoice(move_type='out_refund')
        self.assertEqual(credit._clearvo_build_payload()['documentType'], 'credit_note')

    def test_payload_supplier_from_company(self):
        invoice = self._make_invoice()
        supplier = invoice._clearvo_build_payload()['supplier']
        self.assertEqual(supplier['name'], self.company.name)
        self.assertEqual(supplier['taxId'], self.company.vat)

    def test_payload_buyer_from_partner(self):
        invoice = self._make_invoice()
        buyer = invoice._clearvo_build_payload()['buyer']
        self.assertEqual(buyer['name'], self.partner.name)
        self.assertEqual(buyer['taxId'], self.partner.vat)

    def test_payload_line_tax_code(self):
        invoice = self._make_invoice()
        lines = invoice._clearvo_build_payload()['lines']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['taxCode'], 'S')
        self.assertAlmostEqual(lines[0]['vatRate'], 21.0)

    def test_explicit_peppol_endpoint_included(self):
        self.partner.write({
            'clearvo_peppol_endpoint_id': '12345678',
            'clearvo_peppol_scheme_id': '0088',
        })
        invoice = self._make_invoice()
        buyer = invoice._clearvo_build_payload()['buyer']
        self.assertEqual(buyer.get('endpointId'), '12345678')
        self.assertEqual(buyer.get('endpointSchemeId'), '0088')
        # cleanup
        self.partner.write({'clearvo_peppol_endpoint_id': False, 'clearvo_peppol_scheme_id': False})

    def test_no_peppol_endpoint_when_not_set(self):
        self.partner.write({'clearvo_peppol_endpoint_id': False, 'clearvo_peppol_scheme_id': False})
        invoice = self._make_invoice()
        buyer = invoice._clearvo_build_payload()['buyer']
        self.assertNotIn('endpointId', buyer)

    # ── Tax resolution ────────────────────────────────────────────────────────

    def test_resolve_tax_with_code(self):
        invoice = self._make_invoice()
        line = invoice.invoice_line_ids[0]
        code, rate = invoice._clearvo_resolve_tax(line, line_index=1)
        self.assertEqual(code, 'S')
        self.assertAlmostEqual(rate, 21.0)

    def test_resolve_tax_no_taxes_returns_O(self):
        invoice = self._make_invoice(taxes=self.env['account.tax'])
        line = invoice.invoice_line_ids[0]
        code, rate = invoice._clearvo_resolve_tax(line, line_index=1)
        self.assertEqual(code, 'O')
        self.assertEqual(rate, 0)

    def test_resolve_tax_auto_detects_when_no_override(self):
        # 6% non-zero tax with no override → auto-detects as S (standard)
        invoice = self._make_invoice(taxes=self.tax_no_code)
        line = invoice.invoice_line_ids[0]
        code, rate = invoice._clearvo_resolve_tax(line, line_index=1)
        self.assertEqual(code, 'S')
        self.assertAlmostEqual(rate, 6.0)

    def test_resolve_tax_override_wins_over_auto(self):
        # Manual override of AA (reduced) beats the auto-detected S
        self.tax_no_code.clearvo_tax_code = 'AA'
        invoice = self._make_invoice(taxes=self.tax_no_code)
        line = invoice.invoice_line_ids[0]
        code, rate = invoice._clearvo_resolve_tax(line, line_index=1)
        self.assertEqual(code, 'AA')
        self.tax_no_code.clearvo_tax_code = False  # restore

    # ── Submission ────────────────────────────────────────────────────────────

    def test_send_success_stores_ref_and_status(self):
        invoice = self._make_invoice()
        invoice._clearvo_idempotency_key = False

        mock_resp = self._mock_response(201, {
            'referenceId': 'ref-abc-123',
            'clearanceStatus': 'PENDING',
        })
        with patch('requests.post', return_value=mock_resp):
            invoice._clearvo_send()

        self.assertEqual(invoice.clearvo_status, 'pending')
        self.assertEqual(invoice.clearvo_ref_id, 'ref-abc-123')
        self.assertFalse(invoice.clearvo_error)

    def test_send_409_idempotent_duplicate(self):
        invoice = self._make_invoice()
        invoice.clearvo_idempotency_key = 'existing-key'
        invoice.clearvo_ref_id = 'ref-existing'

        mock_resp = self._mock_response(409, {
            'referenceId': 'ref-existing',
            'clearanceStatus': 'ACCEPTED',
        })
        with patch('requests.post', return_value=mock_resp):
            invoice._clearvo_send()

        self.assertEqual(invoice.clearvo_status, 'accepted')

    def test_send_4xx_stores_error(self):
        invoice = self._make_invoice()
        mock_resp = self._mock_response(422, {
            'error': 'Invalid VAT number format',
        })
        with patch('requests.post', return_value=mock_resp):
            invoice._clearvo_send()

        self.assertEqual(invoice.clearvo_status, 'error')
        self.assertIn('422', invoice.clearvo_error)

    def test_send_timeout_stores_error(self):
        import requests as req
        invoice = self._make_invoice()
        with patch('requests.post', side_effect=req.Timeout):
            invoice._clearvo_send()

        self.assertEqual(invoice.clearvo_status, 'error')
        self.assertIn('timed out', invoice.clearvo_error)

    def test_send_auto_detects_tax_code_when_no_override(self):
        # tax_no_code has amount=6%, no override → auto-detects S → submission proceeds
        invoice = self._make_invoice(taxes=self.tax_no_code)
        mock_resp = self._mock_response(201, {'referenceId': 'r-auto', 'clearanceStatus': 'ACCEPTED'})
        with patch('requests.post', return_value=mock_resp) as mock_post:
            invoice._clearvo_send()
            mock_post.assert_called_once()
        self.assertEqual(invoice.clearvo_status, 'accepted')

    def test_send_generates_idempotency_key(self):
        invoice = self._make_invoice()
        invoice.clearvo_idempotency_key = False
        mock_resp = self._mock_response(201, {'referenceId': 'r1', 'clearanceStatus': 'PENDING'})
        with patch('requests.post', return_value=mock_resp):
            invoice._clearvo_send()
        self.assertTrue(invoice.clearvo_idempotency_key)

    # ── Retry ─────────────────────────────────────────────────────────────────

    def test_retry_resets_idempotency_key(self):
        invoice = self._make_invoice()
        invoice.write({
            'clearvo_status': 'error',
            'clearvo_idempotency_key': 'old-key-from-failed-attempt',
        })
        invoice.action_post()  # put it in posted state

        mock_resp = self._mock_response(201, {'referenceId': 'r2', 'clearanceStatus': 'PENDING'})
        with patch('requests.post', return_value=mock_resp):
            invoice.action_clearvo_retry()

        self.assertNotEqual(invoice.clearvo_idempotency_key, 'old-key-from-failed-attempt')

    def test_retry_raises_if_nothing_to_retry(self):
        invoice = self._make_invoice()
        invoice.clearvo_status = 'accepted'
        with self.assertRaises(UserError):
            invoice.action_clearvo_retry()

    # ── Auto-submit on post ───────────────────────────────────────────────────

    def test_auto_submit_fires_when_enabled(self):
        self.company.clearvo_auto_submit = True
        invoice = self._make_invoice()
        mock_resp = self._mock_response(201, {'referenceId': 'r3', 'clearanceStatus': 'ACCEPTED'})
        with patch('requests.post', return_value=mock_resp) as mock_post:
            invoice.action_post()
            mock_post.assert_called_once()
        self.assertEqual(invoice.clearvo_status, 'accepted')

    def test_no_auto_submit_when_disabled(self):
        self.company.clearvo_auto_submit = False
        invoice = self._make_invoice()
        with patch('requests.post') as mock_post:
            invoice.action_post()
            mock_post.assert_not_called()
        self.assertEqual(invoice.clearvo_status, 'not_sent')

    def test_no_auto_submit_without_api_key(self):
        self.company.write({'clearvo_auto_submit': True, 'clearvo_api_key': False})
        invoice = self._make_invoice()
        with patch('requests.post') as mock_post:
            invoice.action_post()
            mock_post.assert_not_called()
        # restore
        self.company.clearvo_api_key = 'csk_test_odootest'

    # ── Webhook handler ───────────────────────────────────────────────────────

    def test_webhook_updates_status(self):
        invoice = self._make_invoice()
        invoice.write({'clearvo_ref_id': 'ref-wh-1', 'clearvo_status': 'pending'})

        self.env['account.move'].clearvo_handle_webhook({
            'referenceId': 'ref-wh-1',
            'clearanceStatus': 'ACCEPTED',
        })
        self.assertEqual(invoice.clearvo_status, 'accepted')

    def test_webhook_rejected_stores_reason(self):
        invoice = self._make_invoice()
        invoice.write({'clearvo_ref_id': 'ref-wh-2', 'clearvo_status': 'pending'})

        self.env['account.move'].clearvo_handle_webhook({
            'referenceId': 'ref-wh-2',
            'clearanceStatus': 'REJECTED',
            'rejectionReason': 'Invalid VAT number on buyer.',
        })
        self.assertEqual(invoice.clearvo_status, 'rejected')
        self.assertIn('Invalid VAT number', invoice.clearvo_error)

    def test_webhook_unknown_ref_returns_true(self):
        result = self.env['account.move'].clearvo_handle_webhook({
            'referenceId': 'ref-does-not-exist',
            'clearanceStatus': 'ACCEPTED',
        })
        self.assertTrue(result)

    def test_webhook_missing_fields_returns_false(self):
        result = self.env['account.move'].clearvo_handle_webhook({})
        self.assertFalse(result)
