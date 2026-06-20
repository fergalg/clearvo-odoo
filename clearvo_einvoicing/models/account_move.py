import logging
import re
import uuid
import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CLEARVO_API_BASE = 'https://api.clearvo.io/v1'
REQUEST_TIMEOUT = 30

CLEARANCE_STATUS_MAP = {
    'ACCEPTED':  'accepted',
    'REJECTED':  'rejected',
    'PENDING':   'pending',
    'SUBMITTED': 'submitted',
}


class AccountMove(models.Model):
    _inherit = 'account.move'

    clearvo_status = fields.Selection(
        selection=[
            ('not_sent',  'Not submitted'),
            ('pending',   'Pending clearance'),
            ('submitted', 'Submitted'),
            ('accepted',  'Accepted'),
            ('rejected',  'Rejected'),
            ('error',     'Submission error'),
        ],
        string='Clearvo Status',
        default='not_sent',
        copy=False,
        tracking=True,
    )
    clearvo_ref_id = fields.Char(
        string='Clearvo Reference',
        copy=False,
        readonly=True,
        help='Reference ID returned by Clearvo after successful submission.',
    )
    # x-idempotency-key sent with every /v1/send call.
    # Same key = Clearvo returns the cached result (safe dedup for network retries).
    # New key = Clearvo treats it as a fresh submission.
    # Rule: generated fresh on first send AND on every manual retry after an error.
    # Never reset for 'pending' invoices — reusing the key there is intentional dedup.
    clearvo_idempotency_key = fields.Char(copy=False, readonly=True)
    clearvo_error = fields.Text(string='Clearvo Error', copy=False, readonly=True)
    clearvo_submitted_at = fields.Datetime(string='Submitted at', copy=False, readonly=True)

    # ── Button actions ────────────────────────────────────────────────────────

    def action_clearvo_retry(self):
        """Manual retry. Resets the idempotency key so Clearvo treats it as a new submission."""
        retryable = self.filtered(
            lambda m: m.state == 'posted'
            and m.clearvo_status in ('not_sent', 'error')
            and m.company_id.clearvo_api_key
        )
        if not retryable:
            raise UserError(_(
                'Nothing to retry. Either the invoice is already submitted, '
                'or no Clearvo API key is configured for this company.'
            ))
        for move in retryable:
            # Fresh UUID so Clearvo processes this as a new submission rather than
            # returning the cached error from the previous attempt.
            move.clearvo_idempotency_key = str(uuid.uuid4())
            move._clearvo_send()

    def action_clearvo_open_dashboard(self):
        self.ensure_one()
        if not self.clearvo_ref_id:
            raise UserError(_('This invoice has not been submitted to Clearvo yet.'))
        return {
            'type': 'ir.actions.act_url',
            'url': f'https://app.clearvo.io/invoices?ref={self.clearvo_ref_id}',
            'target': 'new',
        }

    # ── Invoice post hook ─────────────────────────────────────────────────────

    def action_post(self):
        result = super().action_post()
        qualifying = self.filtered(
            lambda m: m.move_type in ('out_invoice', 'out_refund')
            and m.company_id.clearvo_api_key
            and m.company_id.clearvo_auto_submit
        )
        for move in qualifying:
            move._clearvo_send()
        return result

    # ── Core submission ───────────────────────────────────────────────────────

    def _clearvo_send(self):
        """Submit this invoice to Clearvo. Never raises — errors are written to the record."""
        self.ensure_one()

        # Generate idempotency key on first ever send.
        # Retries after error reset it in action_clearvo_retry() before calling here.
        if not self.clearvo_idempotency_key:
            self.clearvo_idempotency_key = str(uuid.uuid4())

        try:
            payload = self._clearvo_build_payload()
        except UserError as exc:
            # Tax code or payload validation failed — store the error, don't block posting.
            self.write({'clearvo_status': 'error', 'clearvo_error': str(exc.args[0])})
            return

        try:
            resp = requests.post(
                f'{CLEARVO_API_BASE}/send',
                json=payload,
                headers={
                    'x-api-key': self.company_id.clearvo_api_key,
                    'x-idempotency-key': self.clearvo_idempotency_key,
                    'Content-Type': 'application/json',
                },
                timeout=REQUEST_TIMEOUT,
            )

            if resp.status_code in (200, 201, 202):
                data = resp.json()
                clearance = data.get('clearanceStatus', 'PENDING')
                self.write({
                    'clearvo_status': CLEARANCE_STATUS_MAP.get(clearance, 'pending'),
                    'clearvo_ref_id': data.get('referenceId'),
                    'clearvo_error': False,
                    'clearvo_submitted_at': fields.Datetime.now(),
                })
            elif resp.status_code == 409:
                # Idempotent duplicate — already submitted with this key.
                data = resp.json()
                self.write({
                    'clearvo_status': CLEARANCE_STATUS_MAP.get(
                        data.get('clearanceStatus', 'SUBMITTED'), 'submitted'
                    ),
                    'clearvo_ref_id': data.get('referenceId') or self.clearvo_ref_id,
                    'clearvo_error': False,
                })
            else:
                error = self._clearvo_extract_error(resp)
                _logger.warning('Clearvo submission failed for %s: %s', self.name, error)
                self.write({'clearvo_status': 'error', 'clearvo_error': error})

        except requests.Timeout:
            msg = (
                f'Request timed out after {REQUEST_TIMEOUT}s. '
                'If this persists, use Retry — a fresh idempotency key will be generated.'
            )
            self.write({'clearvo_status': 'error', 'clearvo_error': msg})
        except Exception as exc:
            _logger.exception('Clearvo unexpected error for %s', self.name)
            self.write({'clearvo_status': 'error', 'clearvo_error': str(exc)[:2000]})

    # ── Payload builder ───────────────────────────────────────────────────────

    def _clearvo_build_payload(self):
        """Map account.move to CustomerInvoiceInput. Raises UserError on misconfiguration."""
        self.ensure_one()

        company = self.company_id
        partner = self.partner_id.commercial_partner_id
        country_code = company.country_id.code or ''
        doc_type = 'credit_note' if self.move_type == 'out_refund' else 'invoice'

        payload = {
            'documentType': doc_type,
            'invoiceNumber': self.name,
            'issueDate': (self.invoice_date or fields.Date.today()).isoformat(),
            'currency': self.currency_id.name,
            'country': country_code,
            'supplier': self._clearvo_build_party(
                name=company.name,
                vat=company.vat,
                address_partner=company.partner_id,
                country_code=country_code,
                contact_name=company.partner_id.name,
                contact_phone=company.phone,
                contact_email=company.email,
                peppol_endpoint_id=company.partner_id.clearvo_peppol_endpoint_id,
                peppol_scheme_id=company.partner_id.clearvo_peppol_scheme_id,
            ),
            'buyer': self._clearvo_build_party(
                name=partner.name,
                vat=partner.vat,
                address_partner=partner,
                country_code=partner.country_id.code or '',
                peppol_endpoint_id=partner.clearvo_peppol_endpoint_id,
                peppol_scheme_id=partner.clearvo_peppol_scheme_id,
            ),
            'lines': self._clearvo_build_lines(),
        }

        if self.invoice_date_due:
            payload['dueDate'] = self.invoice_date_due.isoformat()
        if self.ref:
            payload['buyerReference'] = self.ref

        note = self._clearvo_strip_html(self.narration or '')
        if note:
            payload['note'] = note[:500]

        if doc_type == 'credit_note' and self.reversed_entry_id:
            orig = self.reversed_entry_id
            payload['originalInvoiceRef'] = {
                'invoiceNumber': orig.name,
                'issueDate': orig.invoice_date.isoformat() if orig.invoice_date else '',
            }

        payment = self._clearvo_build_payment()
        if payment:
            payload['payment'] = payment

        return payload

    def _clearvo_build_party(self, name, vat, address_partner, country_code,
                              contact_name=None, contact_phone=None, contact_email=None,
                              peppol_endpoint_id=None, peppol_scheme_id=None):
        party = {
            'name': name or '',
            'address': {'city': address_partner.city or '', 'country': country_code},
        }
        if address_partner.street:
            party['address']['street'] = address_partner.street
        if address_partner.zip:
            party['address']['postalCode'] = address_partner.zip
        if vat:
            party['taxId'] = vat
            party['taxIdCountry'] = country_code

        # Send an explicit Peppol endpoint only when the user has configured one.
        # Otherwise omit it — the Clearvo backend derives it from taxId + taxIdCountry.
        if peppol_endpoint_id and peppol_scheme_id:
            party['endpointId'] = peppol_endpoint_id
            party['endpointSchemeId'] = peppol_scheme_id

        if contact_name or contact_phone or contact_email:
            party['contact'] = {}
            if contact_name:
                party['contact']['name'] = contact_name
            if contact_phone:
                party['contact']['phone'] = contact_phone
            if contact_email:
                party['contact']['email'] = contact_email

        return party

    def _clearvo_build_lines(self):
        lines = []
        for i, line in enumerate(self.invoice_line_ids):
            if line.display_type != 'product':
                continue

            tax_code, vat_rate = self._clearvo_resolve_tax(line, line_index=i + 1)

            entry = {
                'description': line.name or (line.product_id.name if line.product_id else ''),
                'quantity': line.quantity,
                'unitPrice': line.price_unit,
                'taxCode': tax_code,
            }
            if vat_rate is not None:
                entry['vatRate'] = vat_rate
            if line.discount:
                entry['discountPercent'] = line.discount
            if line.product_id and line.product_id.default_code:
                entry['sellerItemId'] = line.product_id.default_code

            lines.append(entry)

        return lines

    def _clearvo_build_payment(self):
        payment = {}
        if self.invoice_payment_term_id:
            payment['terms'] = self.invoice_payment_term_id.name
        if self.partner_bank_id and self.partner_bank_id.acc_number:
            iban = re.sub(r'\s', '', self.partner_bank_id.acc_number)
            if re.match(r'^[A-Z]{2}\d{2}', iban):
                payment['iban'] = iban
                payment['method'] = 'bank_transfer'
        if self.invoice_date_due:
            payment['dueDate'] = self.invoice_date_due.isoformat()
        return payment or None

    # ── Tax resolution — explicit config required ─────────────────────────────

    def _clearvo_resolve_tax(self, line, line_index=None):
        """
        Return (tax_code, vat_rate) for an invoice line.

        Uses the manual clearvo_tax_code override if set; otherwise auto-detects
        from the tax rate and fiscal position tags. Lines with no percent taxes → 'O'.
        """
        percent_taxes = line.tax_ids.filtered(lambda t: t.amount_type == 'percent')

        if not percent_taxes:
            return ('O', 0)

        primary = percent_taxes[0]
        tax_code = primary.clearvo_effective_tax_code()
        vat_rate = sum(t.amount for t in percent_taxes)

        return (tax_code, vat_rate)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _clearvo_strip_html(html):
        return re.sub(r'<[^>]+>', ' ', html).strip()

    @staticmethod
    def _clearvo_extract_error(resp):
        try:
            body = resp.json()
            if isinstance(body, dict):
                if 'errors' in body:
                    parts = [
                        f"{e.get('field', '?')}: {e.get('message', '')}"
                        for e in body['errors']
                    ]
                    return f"HTTP {resp.status_code} — " + '; '.join(parts)
                for key in ('error', 'message'):
                    if key in body:
                        return f"HTTP {resp.status_code} — {body[key]}"
        except Exception:
            pass
        return f'HTTP {resp.status_code} — {resp.text[:500]}'

    # ── Webhook receiver (called by controller) ───────────────────────────────

    @api.model
    def clearvo_handle_webhook(self, payload):
        """
        Update clearance status from a Clearvo webhook push.
        Called by controllers/main.py — no cron polling needed.
        """
        ref_id = payload.get('referenceId')
        clearance = payload.get('clearanceStatus')
        if not ref_id or not clearance:
            return False

        move = self.sudo().search([('clearvo_ref_id', '=', ref_id)], limit=1)
        if not move:
            _logger.warning('Clearvo webhook: no invoice found for referenceId %s', ref_id)
            return True  # Acknowledge so Clearvo doesn't retry

        new_status = CLEARANCE_STATUS_MAP.get(clearance)
        if new_status and new_status != move.clearvo_status:
            vals = {'clearvo_status': new_status}
            if new_status == 'rejected':
                vals['clearvo_error'] = payload.get('rejectionReason', 'Rejected by tax authority.')
            move.write(vals)

        return True

    # ── Cron fallback (disabled by default, kept for self-hosted instances
    #    where the Clearvo webhook cannot reach the Odoo server) ───────────────

    @api.model
    def _clearvo_cron_poll_pending(self):
        """Poll /v1/status for pending invoices. Fallback for non-public Odoo instances."""
        pending = self.search([
            ('clearvo_status', '=', 'pending'),
            ('clearvo_ref_id', '!=', False),
            ('company_id.clearvo_api_key', '!=', False),
        ])
        for move in pending:
            try:
                api_key = move.company_id.clearvo_api_key
                resp = requests.get(
                    f'{CLEARVO_API_BASE}/status',
                    params={'referenceId': move.clearvo_ref_id},
                    headers={'x-api-key': api_key},
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    new_status = CLEARANCE_STATUS_MAP.get(
                        data.get('clearanceStatus', 'PENDING'), 'pending'
                    )
                    if new_status != move.clearvo_status:
                        move.clearvo_status = new_status
            except Exception:
                _logger.exception('Clearvo status poll failed for invoice %s', move.name)
