from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    clearvo_api_key = fields.Char(
        related='company_id.clearvo_api_key',
        string='Clearvo API Key',
        readonly=False,
    )
    clearvo_auto_submit = fields.Boolean(
        related='company_id.clearvo_auto_submit',
        string='Auto-submit invoices on post',
        readonly=False,
    )

    def action_clearvo_open_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Connect to Clearvo',
            'res_model': 'clearvo.connect.wizard',
            'view_mode': 'form',
            'target': 'new',
        }
