from odoo import models, fields

# EN16931 / UBL tax category codes that Clearvo accepts.
# These map to the taxCode field on each invoice line.
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


class AccountTax(models.Model):
    _inherit = 'account.tax'

    clearvo_tax_code = fields.Selection(
        selection=TAX_CODE_SELECTION,
        string='Clearvo Tax Code',
        help='EN16931 tax category code sent to Clearvo for lines using this tax. '
             'If blank, Clearvo will auto-detect: S for non-zero rates, AE for 0% rates.',
    )
