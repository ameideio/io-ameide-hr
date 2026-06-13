import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

if "frappe" not in sys.modules:
	frappe_module = types.ModuleType("frappe")
	frappe_module.__path__ = []
	frappe_module.whitelist = lambda **_kwargs: lambda fn: fn
	sys.modules["frappe"] = frappe_module

import hrms.ameide_service_token as service_token_module
from hrms.ameide_service_token import (
	ONBOARDING_SERVICE_ROLE,
	PERMISSION_CONTRACT_VERSION,
	disable_employee,
	ensure_employee,
	ensure_token,
	service_token_contract,
)


class QueryDeadlockError(Exception):
	pass


class _Role:
	def __init__(self, role):
		self.role = role


class _User:
	def __init__(self, email, roles=None):
		self.email = email
		self.first_name = ""
		self.last_name = ""
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


class _Doc:
	def __init__(self, doctype, name=None):
		self.doctype = doctype
		self.name = name
		self.inserted = False
		self.saved = False
		self.insert_errors = []
		self.save_errors = []

	def update(self, values):
		for key, value in values.items():
			setattr(self, key, value)

	def insert(self, ignore_permissions=False):
		if self.insert_errors:
			raise self.insert_errors.pop(0)
		self.inserted = ignore_permissions

	def save(self, ignore_permissions=False):
		if self.save_errors:
			raise self.save_errors.pop(0)
		self.saved = ignore_permissions


class _DB:
	def __init__(self, state):
		self.state = state
		self.committed = False
		self.rollback_count = 0

	def exists(self, doctype, value):
		if doctype == "User":
			return value in self.state["users"]
		if doctype == "Role":
			return value if value in self.state["roles"] else None
		if doctype == "Company":
			return value if value in self.state["companies"] else None
		if doctype == "Employee" and isinstance(value, dict):
			email = value.get("company_email")
			for name, employee in self.state["employees"].items():
				if getattr(employee, "company_email", "") == email:
					return name
			return None
		return None

	def set_value(self, doctype, name, field, value):
		if doctype == "Role":
			self.state["roles"].setdefault(name, _Doc("Role", name)).update({field: value})

	def commit(self):
		self.committed = True

	def rollback(self):
		self.rollback_count += 1


class _Frappe:
	def __init__(self, users):
		self.state = {"users": users, "roles": {}, "companies": {}, "employees": {}}
		self.db = _DB(self.state)
		self.users = self.state["users"]
		self.session = types.SimpleNamespace(user="")

	def get_doc(self, *args):
		if isinstance(args[0], dict):
			doc = args[0]
			doctype = doc["doctype"]
			if doctype == "User":
				user = _User(doc["email"], [row["role"] for row in doc["roles"]])
				user.first_name = doc.get("first_name", "")
				user.last_name = doc.get("last_name", "")
				user.enabled = doc["enabled"]
				user.user_type = doc["user_type"]
				self.users[user.email] = user
				return user
			if doctype == "Company":
				company = _Doc("Company", doc["company_name"])
				company.update(doc)
				self.state["companies"][company.name] = company
				return company
			if doctype == "Employee":
				name = f"EMP-{len(self.state['employees']) + 1:04d}"
				employee = _Doc("Employee", name)
				employee.update(doc)
				self.state["employees"][name] = employee
				return employee
		doctype, name = args
		if doctype == "User":
			return self.users[name]
		if doctype == "Company":
			return self.state["companies"][name]
		if doctype == "Employee":
			return self.state["employees"][name]
		raise KeyError((doctype, name))

	def new_doc(self, doctype):
		name = f"{doctype}-{len(self.state['roles']) + 1}"
		doc = _Doc(doctype, name)
		if doctype == "Role":
			self.state["roles"][name] = doc
		return doc

	def get_roles(self, user):
		return [row.role for row in self.users[user].roles]


class TestAmeideServiceToken(unittest.TestCase):
	@contextmanager
	def _with_frappe(self, users):
		frappe = _Frappe(users)
		user_module = types.ModuleType("frappe.core.doctype.user.user")
		user_module.generate_keys = lambda email: {"api_secret": f"secret-{email}"}
		with (
			patch.dict(
				sys.modules,
				{
					"frappe": frappe,
					"frappe.core": types.ModuleType("frappe.core"),
					"frappe.core.doctype": types.ModuleType("frappe.core.doctype"),
					"frappe.core.doctype.user": types.ModuleType("frappe.core.doctype.user"),
					"frappe.core.doctype.user.user": user_module,
				},
			) as modules,
			patch.object(service_token_module, "frappe", frappe),
		):
			yield modules

	def test_creates_system_user_and_emits_token_result(self):
		with self._with_frappe({}) as modules:
			frappe = modules["frappe"]
			result = ensure_token("svc@example.com", "Service User", '["System Manager"]')

		self.assertFalse(frappe.db.committed)
		self.assertTrue(frappe.users["svc@example.com"].inserted)
		self.assertEqual(frappe.users["svc@example.com"].user_type, "System User")
		self.assertIn(ONBOARDING_SERVICE_ROLE, [row.role for row in frappe.users["svc@example.com"].roles])
		self.assertEqual(result["api_key"], "key-svc@example.com")
		self.assertEqual(result["api_secret"], "secret-svc@example.com")
		self.assertEqual(result["permission_contract_version"], PERMISSION_CONTRACT_VERSION)

	def test_updates_existing_user_roles(self):
		users = {"svc@example.com": _User("svc@example.com", ["System Manager"])}
		with self._with_frappe(users):
			ensure_token("svc@example.com", "Service User", ["System Manager", "HR Manager"])

		self.assertTrue(users["svc@example.com"].saved)
		self.assertEqual(
			[row.role for row in users["svc@example.com"].roles],
			["System Manager", "HR Manager", ONBOARDING_SERVICE_ROLE],
		)

	def test_service_token_contract_reports_required_methods_and_role(self):
		user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			result = service_token_contract()

		self.assertEqual(result["permission_contract_version"], PERMISSION_CONTRACT_VERSION)
		self.assertEqual(result["user"], "svc@example.com")
		self.assertTrue(result["onboarding_role"])
		self.assertEqual(result["ensure_method"], "hrms.ameide_service_token.ensure_employee")
		self.assertEqual(result["disable_method"], "hrms.ameide_service_token.disable_employee")

	def test_ensure_employee_creates_company_and_employee(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			result = ensure_employee(
				email="owner@example.com",
				full_name="Owner One",
				organization_name="E2E Support Customer",
				organization_id="org-e2e-support-customer",
				user_id="user-1",
				idempotency_key="seed-hr-1",
			)

		company = frappe.state["companies"]["E2E Support Customer"]
		employee = frappe.state["employees"][result["name"]]
		self.assertTrue(result["created"])
		self.assertEqual(result["permission_contract_version"], PERMISSION_CONTRACT_VERSION)
		self.assertEqual(company.abbr, "ESC1F847A6B")
		self.assertEqual(company.default_currency, "USD")
		self.assertEqual(company.country, "United States")
		self.assertEqual(employee.company, "E2E Support Customer")
		self.assertEqual(employee.company_email, "owner@example.com")
		self.assertEqual(employee.gender, "Male")
		self.assertEqual(employee.status, "Active")

	def test_company_abbreviation_uses_organization_identity(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			ensure_employee("a@example.com", "Owner A", "Atlas", "org-atlas-a")
			ensure_employee("b@example.com", "Owner B", "Atlas Labs", "org-atlas-b")

		first = frappe.state["companies"]["Atlas"].abbr
		second = frappe.state["companies"]["Atlas Labs"].abbr
		self.assertNotEqual(first, second)
		self.assertTrue(first.startswith("A"))
		self.assertTrue(second.startswith("AL"))

	def test_ensure_employee_updates_existing_employee_idempotently(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			first = ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")
			second = ensure_employee("owner@example.com", "Owner Two", "Atlas", "org-atlas")

		self.assertTrue(first["created"])
		self.assertFalse(second["created"])
		employee = frappe.state["employees"][first["name"]]
		self.assertTrue(employee.saved)
		self.assertEqual(employee.employee_name, "Owner Two")
		self.assertEqual(employee.status, "Active")

	def test_ensure_employee_skips_matching_existing_employee_save(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			first = ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")
			employee = frappe.state["employees"][first["name"]]
			employee.saved = False
			second = ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")

		self.assertFalse(second["created"])
		self.assertFalse(employee.saved)
		self.assertEqual(frappe.db.rollback_count, 0)

	def test_ensure_employee_retries_transient_existing_employee_save(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			first = ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")
			employee = frappe.state["employees"][first["name"]]
			employee.save_errors.append(QueryDeadlockError("deadlock found when trying to get lock"))
			with patch.object(service_token_module, "_sleep_before_retry"):
				second = ensure_employee("owner@example.com", "Owner Two", "Atlas", "org-atlas")

		self.assertFalse(second["created"])
		self.assertEqual(frappe.db.rollback_count, 1)
		self.assertTrue(employee.saved)
		self.assertEqual(employee.employee_name, "Owner Two")

	def test_ensure_employee_does_not_retry_non_transient_save_error(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			first = ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")
			employee = frappe.state["employees"][first["name"]]
			employee.save_errors.append(ValueError("invalid employee state"))
			with self.assertRaises(ValueError):
				ensure_employee("owner@example.com", "Owner Two", "Atlas", "org-atlas")

		self.assertEqual(frappe.db.rollback_count, 0)
		self.assertEqual(employee.save_errors, [])

	def test_ensure_employee_requires_onboarding_service_role(self):
		user = _User("regular@example.com", ["HR User"])
		with self._with_frappe({"regular@example.com": user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "regular@example.com"
			with self.assertRaises(PermissionError):
				ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")

	def test_ensure_employee_requires_organization_identity(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			with self.assertRaises(ValueError):
				ensure_employee("owner@example.com", "Owner One", "Atlas", "")

	def test_disable_employee_marks_employee_inactive(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			created = ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")
			first = disable_employee("owner@example.com", user_id="user-1")
			second = disable_employee("owner@example.com", user_id="user-1")

		employee = frappe.state["employees"][created["name"]]
		self.assertTrue(first["removed"])
		self.assertFalse(second["removed"])
		self.assertTrue(employee.saved)
		self.assertEqual(employee.status, "Inactive")

	def test_disable_employee_retries_transient_save(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			created = ensure_employee("owner@example.com", "Owner One", "Atlas", "org-atlas")
			employee = frappe.state["employees"][created["name"]]
			employee.save_errors.append(QueryDeadlockError("deadlock found when trying to get lock"))
			with patch.object(service_token_module, "_sleep_before_retry"):
				result = disable_employee("owner@example.com", user_id="user-1")

		self.assertTrue(result["removed"])
		self.assertEqual(frappe.db.rollback_count, 1)
		self.assertTrue(employee.saved)
		self.assertEqual(employee.status, "Inactive")

	def test_disable_employee_absent_is_idempotent(self):
		service_user = _User("svc@example.com", [ONBOARDING_SERVICE_ROLE])
		with self._with_frappe({"svc@example.com": service_user}) as modules:
			frappe = modules["frappe"]
			frappe.session.user = "svc@example.com"
			result = disable_employee("missing@example.com", user_id="user-1")

		self.assertFalse(result["removed"])
		self.assertEqual(result["permission_contract_version"], PERMISSION_CONTRACT_VERSION)


if __name__ == "__main__":
	unittest.main()
