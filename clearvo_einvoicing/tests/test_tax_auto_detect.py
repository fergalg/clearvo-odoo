from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install', 'clearvo')
class TestClearvoTaxAutoDetect(TransactionCase):

    def _tax(self, name, amount, tags=(), override=False):
        tax = self.env['account.tax'].create({
            'name': name,
            'amount': amount,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'clearvo_tax_code': override or False,
        })
        if tags:
            tag_records = self.env['account.account.tag'].create([
                {'name': t, 'applicability': 'taxes'} for t in tags
            ])
            tax.tax_tag_ids = tag_records
        return tax

    def test_positive_rate_auto_S(self):
        self.assertEqual(self._tax('21% VAT', 21)._clearvo_detect_code(), 'S')

    def test_positive_rate_low_auto_S(self):
        self.assertEqual(self._tax('5% VAT', 5)._clearvo_detect_code(), 'S')

    def test_zero_rate_no_tags_auto_E(self):
        self.assertEqual(self._tax('0% Exempt', 0)._clearvo_detect_code(), 'E')

    def test_zero_rate_intra_eu_tag_auto_K(self):
        tax = self._tax('0% Intra-EU', 0, tags=['Intra-EU supply'])
        self.assertEqual(tax._clearvo_detect_code(), 'K')

    def test_zero_rate_export_tag_auto_G(self):
        tax = self._tax('0% Export', 0, tags=['Export outside EU'])
        self.assertEqual(tax._clearvo_detect_code(), 'G')

    def test_zero_rate_reverse_charge_tag_auto_AE(self):
        tax = self._tax('0% RC', 0, tags=['Reverse charge'])
        self.assertEqual(tax._clearvo_detect_code(), 'AE')

    def test_fixed_tax_auto_O(self):
        tax = self.env['account.tax'].create({
            'name': 'Fixed fee',
            'amount': 5.0,
            'amount_type': 'fixed',
            'type_tax_use': 'sale',
        })
        self.assertEqual(tax._clearvo_detect_code(), 'O')

    def test_manual_override_wins(self):
        tax = self._tax('21% VAT', 21, override='AA')
        self.assertEqual(tax.clearvo_effective_tax_code(), 'AA')

    def test_no_override_uses_auto(self):
        tax = self._tax('21% VAT', 21)
        self.assertEqual(tax.clearvo_effective_tax_code(), 'S')

    def test_auto_display_field_contains_code_and_label(self):
        tax = self._tax('21% VAT', 21)
        self.assertIn('S', tax.clearvo_tax_code_auto)
        self.assertIn('Standard', tax.clearvo_tax_code_auto)
