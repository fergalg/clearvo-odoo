from unittest.mock import patch, MagicMock

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


@tagged('post_install', '-at_install', 'clearvo')
class TestClearvoConnectWizard(TransactionCase):

    def _make_wizard(self, api_key='csk_test_abc123'):
        return self.env['clearvo.connect.wizard'].create({'api_key': api_key})

    def _mock_response(self, status_code):
        mock = MagicMock()
        mock.status_code = status_code
        mock.text = ''
        return mock

    def test_validate_key_success(self):
        wizard = self._make_wizard()
        with patch('requests.get', return_value=self._mock_response(200)):
            wizard._clearvo_validate_key('csk_test_abc123')  # should not raise

    def test_validate_key_invalid_raises(self):
        wizard = self._make_wizard()
        with patch('requests.get', return_value=self._mock_response(401)):
            with self.assertRaises(UserError) as cm:
                wizard._clearvo_validate_key('csk_test_bad')
            self.assertIn('Invalid API key', str(cm.exception))

    def test_validate_key_forbidden_raises(self):
        wizard = self._make_wizard()
        with patch('requests.get', return_value=self._mock_response(403)):
            with self.assertRaises(UserError):
                wizard._clearvo_validate_key('csk_test_bad')

    def test_validate_key_server_error_raises(self):
        wizard = self._make_wizard()
        with patch('requests.get', return_value=self._mock_response(500)):
            with self.assertRaises(UserError) as cm:
                wizard._clearvo_validate_key('csk_test_abc')
            self.assertIn('500', str(cm.exception))

    def test_validate_key_timeout_raises(self):
        import requests as req
        wizard = self._make_wizard()
        with patch('requests.get', side_effect=req.Timeout):
            with self.assertRaises(UserError) as cm:
                wizard._clearvo_validate_key('csk_test_abc')
            self.assertIn('timed out', str(cm.exception))

    def test_connect_saves_key_to_company(self):
        self.env.company.clearvo_api_key = False
        wizard = self._make_wizard(api_key='csk_test_newkey')

        with patch('requests.get', return_value=self._mock_response(200)):
            with patch('requests.post', return_value=self._mock_response(201)):
                wizard.action_connect()

        self.assertEqual(self.env.company.clearvo_api_key, 'csk_test_newkey')
        self.assertTrue(self.env.company.clearvo_auto_submit)

    def test_connect_empty_key_raises(self):
        wizard = self._make_wizard(api_key='   ')
        with self.assertRaises(UserError) as cm:
            wizard.action_connect()
        self.assertIn('Please enter', str(cm.exception))

    def test_open_signup_returns_url_action(self):
        wizard = self._make_wizard()
        result = wizard.action_open_signup()
        self.assertEqual(result['type'], 'ir.actions.act_url')
        self.assertIn('clearvo.io', result['url'])
