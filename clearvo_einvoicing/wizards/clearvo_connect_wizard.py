import logging

import requests

from odoo import models, fields, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CLEARVO_API_BASE = 'https://api.clearvo.io/v1'


class ClearvoConnectWizard(models.TransientModel):
    _name = 'clearvo.connect.wizard'
    _description = 'Connect to Clearvo'

    api_key = fields.Char(
        string='API Key',
        help='Paste your Clearvo API key here. Create one at app.clearvo.io → Settings → API Keys.',
    )
    # Read-only feedback shown after a successful test
    connection_ok = fields.Boolean(default=False, readonly=True)
    connection_message = fields.Char(readonly=True)

    def action_open_signup(self):
        """Open clearvo.io registration in a new browser tab."""
        return {
            'type': 'ir.actions.act_url',
            'url': 'https://app.clearvo.io/register?source=odoo&utm_source=odoo-module&utm_medium=wizard',
            'target': 'new',
        }

    def action_connect(self):
        """Validate the API key against the Clearvo API, then save it to the current company."""
        self.ensure_one()

        if not self.api_key or not self.api_key.strip():
            raise UserError(_('Please enter an API key.'))

        key = self.api_key.strip()
        self._clearvo_validate_key(key)

        # Save to the current company (sudo so non-admin users can complete onboarding)
        self.env.company.sudo().write({
            'clearvo_api_key': key,
            'clearvo_auto_submit': True,
        })

        # Register a Clearvo webhook so status updates are pushed rather than polled.
        # Best-effort: failure here does not prevent the connection from completing.
        # The fallback cron (disabled by default) can be re-enabled on private instances
        # where the Odoo server is not reachable from the public internet.
        self._clearvo_register_webhook(key)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Clearvo connected'),
                'message': _(
                    'Your invoices will now be submitted automatically '
                    'when you post them in Odoo.'
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_disconnect(self):
        """Remove the API key from the current company."""
        self.env.company.sudo().write({'clearvo_api_key': False})
        return {'type': 'ir.actions.act_window_close'}

    def _clearvo_register_webhook(self, key):
        """
        Register an Odoo webhook URL with Clearvo so status changes are pushed
        rather than polled. The URL is derived from the Odoo base URL.
        """
        try:
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
            if not base_url:
                return
            webhook_url = f'{base_url.rstrip("/")}/clearvo/webhook'
            resp = requests.post(
                f'{CLEARVO_API_BASE}/webhooks',
                json={
                    'url': webhook_url,
                    'events': ['invoice.accepted', 'invoice.rejected', 'invoice.submitted'],
                    'description': f'Odoo auto-registered ({self.env.company.name})',
                },
                headers={
                    'x-api-key': key,
                    'Content-Type': 'application/json',
                },
                timeout=10,
            )
            if resp.status_code in (200, 201, 409):
                # 409 = webhook URL already registered, which is fine.
                _logger.info('Clearvo webhook registered at %s (HTTP %s)', webhook_url, resp.status_code)
            else:
                _logger.warning(
                    'Clearvo webhook registration returned HTTP %s: %s',
                    resp.status_code, resp.text[:200],
                )
        except Exception:
            _logger.exception('Clearvo webhook registration failed — falling back to cron poll')

    def _clearvo_validate_key(self, key):
        """Call the Clearvo API to confirm the key is valid. Raises UserError on failure."""
        try:
            resp = requests.get(
                f'{CLEARVO_API_BASE}/invoices',
                params={'limit': 1},
                headers={'x-api-key': key},
                timeout=10,
            )
        except requests.Timeout:
            raise UserError(_(
                "Could not reach Clearvo — the request timed out. "
                "Check your internet connection and try again."
            ))
        except Exception as e:
            raise UserError(_("Connection error: %s") % str(e))

        if resp.status_code == 200:
            return  # valid

        if resp.status_code in (401, 403):
            raise UserError(_(
                "Invalid API key. Double-check the key from your Clearvo dashboard "
                "(Settings → API Keys) and try again."
            ))

        raise UserError(_(
            "Clearvo returned an unexpected error (HTTP %d). "
            "Try again or contact support at help.clearvo.io."
        ) % resp.status_code)
