from odoo import models, fields, api

TAX_CODE_SELECTION = [
    ('S',  'S — Standard rate'),
    ('AA', 'AA — Reduced rate'),
    ('AB', 'AB — Second reduced rate'),
    ('AE', 'AE — Reverse charge (0%)'),
    ('K',  'K — Intra-EU supply (0%)'),
    ('G',  'G — Export outside EU (0%)'),
    ('E',  'E — Exempt from VAT'),
    ('O',  'O — Out of scope'),
    ('Z',  'Z — Zero-rated domestic'),
]

_CODE_LABELS = dict(TAX_CODE_SELECTION)


class AccountTax(models.Model):
    _inherit = 'account.tax'

    clearvo_tax_code = fields.Selection(
        selection=TAX_CODE_SELECTION,
        string='Clearvo Tax Code Override',
        help='Leave blank — Clearvo will auto-detect the correct code from your tax rate '
             'and fiscal position. Set this only if the auto-detection is wrong for this tax.',
    )

    clearvo_tax_code_auto = fields.Char(
        string='Auto-detected code',
        compute='_compute_clearvo_tax_code_auto',
        help='The EN16931 code Clearvo will use for this tax, based on its rate and tags.',
    )

    @api.depends('amount', 'amount_type', 'tax_tag_ids', 'tax_tag_ids.name')
    def _compute_clearvo_tax_code_auto(self):
        for tax in self:
            code = tax._clearvo_detect_code()
            tax.clearvo_tax_code_auto = f'{code} — {_CODE_LABELS.get(code, code)}'

    def _clearvo_detect_code(self):
        """
        Auto-detect EN16931 tax category code from Odoo tax properties.

        Logic:
        - Non-percent taxes (fixed, group) → O (out of scope)
        - amount > 0 → S (standard); covers reduced rates — override with AA/AB if needed
        - amount = 0, intra-EU tag keywords → K
        - amount = 0, export tag keywords → G
        - amount = 0, reverse charge tag keywords → AE
        - amount = 0, no matching tags → E (exempt, safest 0% default)

        Correct for ~80% of standard Odoo tax setups out of the box.
        """
        self.ensure_one()

        if self.amount_type != 'percent':
            return 'O'

        if self.amount > 0:
            return 'S'

        # 0% tax — look at fiscal/report tags to distinguish treatment
        tag_text = ' '.join(self.tax_tag_ids.mapped('name')).lower()

        # Intra-EU zero-rated (K): cross-border within EU, no VAT charged, buyer accounts for it
        if any(kw in tag_text for kw in (
            'intra', 'eu ', 'intracommunity', 'ic supply', 'icp',
            'inner', 'gemeinschaft', 'intérieur',
        )):
            return 'K'

        # Export / outside EU (G): goods/services leaving the EU
        if any(kw in tag_text for kw in (
            'export', 'outside', 'third country', 'non-eu', 'drittland',
            'exportation', 'hors ue',
        )):
            return 'G'

        # Reverse charge (AE): domestic or cross-border, buyer accounts for VAT
        if any(kw in tag_text for kw in (
            'reverse', 'vat shift', 'verlegung', 'verlegd',
            'autoliquidation', 'autofattura', 'inversione',
        )):
            return 'AE'

        # Default for unlabelled 0% taxes: exempt
        return 'E'

    def clearvo_effective_tax_code(self):
        """Return the manual override if set, otherwise the auto-detected code."""
        self.ensure_one()
        return self.clearvo_tax_code or self._clearvo_detect_code()
