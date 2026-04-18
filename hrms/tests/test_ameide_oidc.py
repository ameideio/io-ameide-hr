import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import frappe
from frappe.auth import LoginManager
from frappe.tests import UnitTestCase
from frappe.utils.password import set_encrypted_password

from hrms.ameide_sso.bootstrap import ensure_social_login_key_from_env
from hrms.www.auth.ameide_oidc import index as ameide_oidc_index
from hrms.www.auth.ameide_oidc import logout as ameide_oidc_logout
from hrms.www.auth.ameide_oidc import redirect as ameide_oidc_redirect


class _OidcHandler(BaseHTTPRequestHandler):
	def log_message(self, format, *args):
		return

	def do_POST(self):
		if self.path != "/protocol/openid-connect/token":
			self.send_response(404)
			self.end_headers()
			return

		self.send_response(200)
		self.send_header("Content-Type", "application/json")
		self.end_headers()
		self.wfile.write(json.dumps({"access_token": "access-token", "token_type": "Bearer"}).encode("utf-8"))

	def do_GET(self):
		if self.path != "/protocol/openid-connect/userinfo":
			self.send_response(404)
			self.end_headers()
			return

		self.send_response(200)
		self.send_header("Content-Type", "application/json")
		self.end_headers()
		self.wfile.write(
			json.dumps(
				{
					"sub": "sub-123",
					"email": "alice@example.com",
					"given_name": "Alice",
					"family_name": "Doe",
				}
			).encode("utf-8")
		)


class TestAmeideOidc(UnitTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._server = ThreadingHTTPServer(("127.0.0.1", 0), _OidcHandler)
		cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
		cls._thread.start()

		host, port = cls._server.server_address
		cls._issuer = f"http://{host}:{port}"

		cls._provider_name = "ameide"
		cls._ensure_social_login_key()
		frappe.conf.ameide_sso_provider = cls._provider_name

	@classmethod
	def tearDownClass(cls):
		try:
			if frappe.db.exists("Social Login Key", cls._provider_name):
				frappe.delete_doc("Social Login Key", cls._provider_name, force=True)
		finally:
			cls._server.shutdown()
			cls._server.server_close()
			super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.local.form_dict = frappe._dict()
		frappe.local.response = {}
		frappe.local.login_manager = LoginManager()
		frappe.session.user = "Guest"

	def test_start_redirect_builds_authorize_url_with_state(self):
		frappe.local.form_dict.update({"redirect-to": "/hrms"})
		ameide_oidc_index.get_context()

		location = frappe.local.response.get("location")
		self.assertTrue(location)

		parsed = urlparse(location)
		self.assertEqual(parsed.scheme, "http")
		self.assertEqual(parsed.netloc, urlparse(self._issuer).netloc)
		self.assertEqual(parsed.path, "/protocol/openid-connect/auth")

		query = parse_qs(parsed.query)
		self.assertIn("state", query)
		state = query["state"][0]

		state_dict = json.loads(base64.b64decode(state).decode("utf-8"))
		self.assertEqual(state_dict.get("redirect_to"), "/hrms")
		self.assertTrue(state_dict.get("token"))

	def test_callback_creates_system_user_and_redirects(self):
		frappe.local.form_dict.update({"redirect-to": "/hrms"})
		ameide_oidc_index.get_context()
		state = parse_qs(urlparse(frappe.local.response["location"]).query)["state"][0]

		frappe.local.response = {}
		frappe.local.form_dict = frappe._dict({"code": "dummy-code", "state": state})
		ameide_oidc_redirect.get_context()

		self.assertEqual(frappe.local.response.get("type"), "redirect")
		self.assertTrue((frappe.local.response.get("location") or "").endswith("/hrms"))

		self.assertTrue(frappe.db.exists("User", "alice@example.com"))
		self.assertEqual(frappe.db.get_value("User", "alice@example.com", "user_type"), "System User")
		self.assertIn("Employee", frappe.get_roles("alice@example.com"))

	def test_logout_redirects_to_end_session(self):
		frappe.session.user = "alice@example.com"
		frappe.local.form_dict.update({"post-logout-redirect": "/hrms"})

		ameide_oidc_logout.get_context()

		location = frappe.local.response.get("location")
		self.assertTrue(location)
		self.assertTrue(location.startswith(f"{self._issuer}/protocol/openid-connect/logout?"))
		self.assertIn("client_id=client-id", location)
		self.assertIn("post_logout_redirect_uri=", location)

	def test_bootstrap_creates_or_updates_social_login_key_from_env(self):
		provider_name = "ameide-bootstrap"
		with patch.dict(
			os.environ,
			{
				"AMEIDE_OIDC_PROVIDER_NAME": provider_name,
				"AMEIDE_OIDC_PROVIDER_LABEL": "Ameide Bootstrap",
				"AMEIDE_OIDC_ISSUER_URL": self._issuer,
				"AMEIDE_OIDC_CLIENT_ID": "bootstrap-client",
				"AMEIDE_OIDC_CLIENT_SECRET": "bootstrap-secret",
				"AMEIDE_OIDC_REDIRECT_PATH": "/auth/ameide-oidc/redirect",
				"AMEIDE_OIDC_USER_ID_PROPERTY": "sub",
			},
			clear=False,
		):
			ensure_social_login_key_from_env()

		doc = frappe.get_doc("Social Login Key", provider_name)
		self.assertEqual(doc.provider_name, "Ameide Bootstrap")
		self.assertEqual(doc.client_id, "bootstrap-client")
		self.assertEqual(doc.base_url, self._issuer)
		self.assertEqual(doc.redirect_url, "/auth/ameide-oidc/redirect")
		self.assertTrue(doc.enable_social_login)

		with patch.dict(
			os.environ,
			{
				"AMEIDE_OIDC_PROVIDER_NAME": provider_name,
				"AMEIDE_OIDC_PROVIDER_LABEL": "Ameide Bootstrap",
				"AMEIDE_OIDC_ISSUER_URL": self._issuer,
				"AMEIDE_OIDC_CLIENT_ID": "bootstrap-client-updated",
				"AMEIDE_OIDC_CLIENT_SECRET": "bootstrap-secret-updated",
				"AMEIDE_OIDC_REDIRECT_PATH": "/auth/ameide-oidc/redirect",
				"AMEIDE_OIDC_USER_ID_PROPERTY": "sub",
			},
			clear=False,
		):
			ensure_social_login_key_from_env()

		self.assertEqual(
			frappe.db.get_value("Social Login Key", provider_name, "client_id"),
			"bootstrap-client-updated",
		)
		self.assertTrue(frappe.db.exists("Social Login Key", provider_name))
		frappe.delete_doc("Social Login Key", provider_name, force=True)
		frappe.db.commit()

	@classmethod
	def _ensure_social_login_key(cls):
		if frappe.db.exists("Social Login Key", cls._provider_name):
			return

		doc = frappe.get_doc(
			{
				"doctype": "Social Login Key",
				"name": cls._provider_name,
				"provider_name": "Ameide",
				"client_id": "client-id",
				"base_url": cls._issuer,
				"custom_base_url": 1,
				"authorize_url": "/protocol/openid-connect/auth",
				"access_token_url": "/protocol/openid-connect/token",
				"api_endpoint": "/protocol/openid-connect/userinfo",
				"redirect_url": "/auth/ameide-oidc/redirect",
				"user_id_property": "sub",
				"enable_social_login": 1,
			}
		).insert(ignore_permissions=True)

		set_encrypted_password("Social Login Key", doc.name, "client_secret", "client-secret")
		frappe.db.commit()
