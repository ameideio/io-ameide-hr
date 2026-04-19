import importlib.util
import sys
import types
import unittest
from pathlib import Path


class FakeFrappe(types.ModuleType):
	def __getattr__(self, name):
		if name in {"session", "form_dict"}:
			return getattr(self.local, name)
		raise AttributeError(name)


class TestAmeideOidcPages(unittest.TestCase):
	def _restore_module(self, name, module):
		if module is None:
			sys.modules.pop(name, None)
			return
		sys.modules[name] = module

	def _load_module(self, relative_path):
		original_frappe = sys.modules.get("frappe")
		original_hrms = sys.modules.get("hrms")
		original_hrms_www = sys.modules.get("hrms.www")
		original_hrms_www_auth = sys.modules.get("hrms.www.auth")
		original_hrms_www_auth_ameide_oidc = sys.modules.get("hrms.www.auth.ameide_oidc")
		original_index = sys.modules.get("hrms.www.auth.ameide_oidc.index")
		original_redirect = sys.modules.get("hrms.www.auth.ameide_oidc.redirect")
		original_logout = sys.modules.get("hrms.www.auth.ameide_oidc.logout")
		frappe = FakeFrappe("frappe")
		frappe.Redirect = type("Redirect", (Exception,), {})
		frappe.local = types.SimpleNamespace(
			flags=types.SimpleNamespace(),
			login_manager=types.SimpleNamespace(logout=lambda: setattr(self, "logout_called", True)),
			form_dict={},
			session=types.SimpleNamespace(data={}),
		)

		index = types.ModuleType("hrms.www.auth.ameide_oidc.index")
		index.get_context = lambda context=None: setattr(self, "index_context", context)
		redirect = types.ModuleType("hrms.www.auth.ameide_oidc.redirect")
		redirect.get_context = lambda context=None: setattr(self, "redirect_context", context)
		logout = types.ModuleType("hrms.www.auth.ameide_oidc.logout")
		logout.get_context = lambda context=None: setattr(self, "logout_context", context)

		hrms = types.ModuleType("hrms")
		hrms.__path__ = [str(Path(__file__).resolve().parents[1])]
		hrms_www = types.ModuleType("hrms.www")
		hrms_www.__path__ = [str(Path(__file__).resolve().parents[1] / "www")]
		hrms_www_auth = types.ModuleType("hrms.www.auth")
		hrms_www_auth.__path__ = [str(Path(__file__).resolve().parents[1] / "www" / "auth")]
		hrms_www_auth_ameide_oidc = types.ModuleType("hrms.www.auth.ameide_oidc")
		hrms_www_auth_ameide_oidc.__path__ = [
			str(Path(__file__).resolve().parents[1] / "www" / "auth" / "ameide_oidc")
		]

		self.addCleanup(self._restore_module, "frappe", original_frappe)
		self.addCleanup(self._restore_module, "hrms", original_hrms)
		self.addCleanup(self._restore_module, "hrms.www", original_hrms_www)
		self.addCleanup(self._restore_module, "hrms.www.auth", original_hrms_www_auth)
		self.addCleanup(
			self._restore_module,
			"hrms.www.auth.ameide_oidc",
			original_hrms_www_auth_ameide_oidc,
		)
		self.addCleanup(self._restore_module, "hrms.www.auth.ameide_oidc.index", original_index)
		self.addCleanup(self._restore_module, "hrms.www.auth.ameide_oidc.redirect", original_redirect)
		self.addCleanup(self._restore_module, "hrms.www.auth.ameide_oidc.logout", original_logout)
		sys.modules["frappe"] = frappe
		sys.modules["hrms"] = hrms
		sys.modules["hrms.www"] = hrms_www
		sys.modules["hrms.www.auth"] = hrms_www_auth
		sys.modules["hrms.www.auth.ameide_oidc"] = hrms_www_auth_ameide_oidc
		sys.modules["hrms.www.auth.ameide_oidc.index"] = index
		sys.modules["hrms.www.auth.ameide_oidc.redirect"] = redirect
		sys.modules["hrms.www.auth.ameide_oidc.logout"] = logout

		module_path = Path(__file__).resolve().parents[1] / relative_path
		spec = importlib.util.spec_from_file_location(f"hrms_{relative_path.replace('/', '_')}", module_path)
		module = importlib.util.module_from_spec(spec)
		assert spec and spec.loader
		spec.loader.exec_module(module)
		return module, frappe

	def _load_hooks(self):
		module_path = Path(__file__).resolve().parents[1] / "hooks.py"
		spec = importlib.util.spec_from_file_location("hrms_hooks_under_test", module_path)
		module = importlib.util.module_from_spec(spec)
		assert spec and spec.loader
		spec.loader.exec_module(module)
		return module

	def test_login_page_redirects_to_oidc(self):
		module, frappe = self._load_module("www/login.py")
		context = types.SimpleNamespace()
		frappe.local.form_dict = {"redirect_to": "/hrms/team"}
		module.get_context(context)
		self.assertIs(self.index_context, context)

	def test_auth_entrypoint_redirects_to_oidc(self):
		module, frappe = self._load_module("www/ameide_oidc.py")
		context = types.SimpleNamespace()
		frappe.local.form_dict = {"redirect-to": "/hrms"}
		module.get_context(context)
		self.assertIs(self.index_context, context)

	def test_auth_redirect_page_completes_login(self):
		module, frappe = self._load_module("www/ameide_oidc_redirect.py")
		context = types.SimpleNamespace()
		frappe.local.form_dict = {"code": "code-123", "state": "state-456"}
		module.get_context(context)
		self.assertIs(self.redirect_context, context)

	def test_logout_page_uses_keycloak_logout(self):
		module, frappe = self._load_module("www/logout.py")
		context = types.SimpleNamespace()
		frappe.local.session.data["ameide_oidc_id_token"] = "token-123"
		module.get_context(context)
		self.assertIs(self.logout_context, context)

	def test_hooks_expose_sales_equivalent_ameide_routes(self):
		hooks = self._load_hooks()
		self.assertIn(
			{"from_route": "/auth/ameide-oidc", "to_route": "ameide_oidc"},
			hooks.website_route_rules,
		)
		self.assertIn(
			{"from_route": "/auth/ameide-oidc/redirect", "to_route": "ameide_oidc_redirect"},
			hooks.website_route_rules,
		)
		self.assertIn(
			{"from_route": "/auth/ameide-oidc/logout", "to_route": "ameide_oidc_logout"},
			hooks.website_route_rules,
		)
		self.assertIn(
			{"source": "/login", "target": "/auth/ameide-oidc"},
			hooks.website_redirects,
		)
		self.assertIn(
			{"source": "/logout", "target": "/auth/ameide-oidc/logout"},
			hooks.website_redirects,
		)


if __name__ == "__main__":
	unittest.main()
