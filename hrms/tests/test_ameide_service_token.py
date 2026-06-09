import sys
import types
import unittest
from unittest.mock import patch

frappe_module = sys.modules.setdefault("frappe", types.ModuleType("frappe"))
frappe_module.whitelist = lambda **_kwargs: lambda fn: fn
from hrms.ameide_service_token import ensure_token


class _Role:
	def __init__(self, role):
		self.role = role


class _User:
	def __init__(self, email, roles=None):
		self.email = email
		self.enabled = 0
		self.user_type = "Website User"
		self.roles = [_Role(role) for role in (roles or [])]
		self.api_key = f"key-{email}"
		self.inserted = False
		self.saved = False

	def insert(self, ignore_permissions=False):
		self.inserted = ignore_permissions

	def append(self, field, value):
		if field == "roles":
			self.roles.append(_Role(value["role"]))

	def save(self, ignore_permissions=False):
		self.saved = ignore_permissions


class _DB:
	def __init__(self, users):
		self.users = users
		self.committed = False

	def exists(self, doctype, email):
		return doctype == "User" and email in self.users

	def commit(self):
		self.committed = True


class _Frappe:
	def __init__(self, users):
		self.db = _DB(users)
		self.users = users

	def get_doc(self, *args):
		if isinstance(args[0], dict):
			doc = args[0]
			user = _User(doc["email"], [row["role"] for row in doc["roles"]])
			user.enabled = doc["enabled"]
			user.user_type = doc["user_type"]
			self.users[user.email] = user
			return user
		return self.users[args[1]]


class TestAmeideServiceToken(unittest.TestCase):
	def _with_frappe(self, users):
		frappe = _Frappe(users)
		user_module = types.ModuleType("frappe.core.doctype.user.user")
		user_module.generate_keys = lambda email: {"api_secret": f"secret-{email}"}
		return patch.dict(
			sys.modules,
			{
				"frappe": frappe,
				"frappe.core": types.ModuleType("frappe.core"),
				"frappe.core.doctype": types.ModuleType("frappe.core.doctype"),
				"frappe.core.doctype.user": types.ModuleType("frappe.core.doctype.user"),
				"frappe.core.doctype.user.user": user_module,
			},
		), frappe

	def test_creates_system_user_and_emits_token_result(self):
		with self._with_frappe({})[0] as modules:
			frappe = modules["frappe"]
			result = ensure_token("svc@example.com", "Service User", '["System Manager"]')

		self.assertFalse(frappe.db.committed)
		self.assertTrue(frappe.users["svc@example.com"].inserted)
		self.assertEqual(frappe.users["svc@example.com"].user_type, "System User")
		self.assertEqual(result["api_key"], "key-svc@example.com")
		self.assertEqual(result["api_secret"], "secret-svc@example.com")

	def test_updates_existing_user_roles(self):
		users = {"svc@example.com": _User("svc@example.com", ["System Manager"])}
		with self._with_frappe(users)[0]:
			ensure_token("svc@example.com", "Service User", ["System Manager", "HR Manager"])

		self.assertTrue(users["svc@example.com"].saved)
		self.assertEqual(
			[row.role for row in users["svc@example.com"].roles], ["System Manager", "HR Manager"]
		)
