from odoo import models, fields


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Required for Peppol countries where the endpoint ID cannot be auto-derived
    # from the VAT number: NL (KvK), AT (9915/VAT), SK (IČO), IS (Kennitala),
    # NZ (NZBN), AE, CH (UID).
    # For the 15 auto-derive countries (BE, DK, NO, FI, SE, HR, IE, LU, SI, EE,
    # LV, LT, AU, SG, JP) these fields can be left blank.
    clearvo_peppol_endpoint_id = fields.Char(
        string='Peppol Endpoint ID',
        help=(
            'Required for countries where the endpoint cannot be auto-derived from the VAT number '
            '(NL=KvK number, AT=VAT number, SK=IČO, IS=Kennitala, NZ=NZBN, CH=UID). '
            'Leave blank for BE, DK, NO, FI, SE, HR, IE, LU, SI, EE, LV, LT, AU, SG, JP — '
            'the module derives these from the VAT number automatically.'
        ),
    )
    clearvo_peppol_scheme_id = fields.Char(
        string='Peppol Scheme ID',
        help=(
            'EAS scheme code for the endpoint above. '
            'Examples: 0088 (NL/NZ), 9915 (AT), 9917 (SK), 0196 (IS), 0183 (CH).'
        ),
    )
