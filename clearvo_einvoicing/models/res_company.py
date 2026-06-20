from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    clearvo_api_key = fields.Char(
        string='Clearvo API Key',
        groups='base.group_system',
        help='API key from your Clearvo dashboard (Settings → API Keys). '
             'Starts with csk_live_ for production or csk_test_ for sandbox.',
    )
    clearvo_auto_submit = fields.Boolean(
        string='Auto-submit invoices on post',
        default=True,
        help='When enabled, invoices are submitted to Clearvo automatically '
             'when posted. Disable to submit manually.',
    )
    clearvo_webhook_secret = fields.Char(
        string='Clearvo Webhook Secret',
        groups='base.group_system',
        help='Signing secret returned by Clearvo when the webhook was registered. '
             'Used to verify the authenticity of incoming status push notifications.',
    )
