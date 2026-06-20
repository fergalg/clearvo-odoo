{
    'name': 'Clearvo E-Invoicing',
    'version': '17.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Submit invoices to Clearvo for e-invoicing compliance in France, Germany, Belgium and more.',
    'description': """
Clearvo E-Invoicing
===================
Automatically submit invoices to national tax authorities the moment you post in Odoo.

Supported countries: France (Factur-X), Germany (XRechnung/ZUGFeRD),
Belgium (Peppol BIS 3.0), Italy, Poland, Spain, Portugal and more.

- Auto-submit on invoice post
- Real-time clearance status on every invoice
- Manual retry for failed submissions
- Per-company API key (multi-company safe)
    """,
    'author': 'Clearvo',
    'website': 'https://clearvo.io',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/account_tax_views.xml',
        'views/account_move_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'views/clearvo_connect_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'images': [
        'static/description/banner.png',
        'static/description/screenshot_invoice.png',
        'static/description/screenshot_wizard.png',
        'static/description/screenshot_settings.png',
    ],
}
