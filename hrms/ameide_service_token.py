import json
import os
import random
import time

import frappe

ONBOARDING_SERVICE_ROLE = "Ameide HR Onboarding"
EMPLOYEE_DOCTYPE = "Employee"
COMPANY_DOCTYPE = "Company"
AMEIDE_ORGANIZATION_ID_FIELD = "ameide_organization_id"
AMEIDE_ORGANIZATION_NAME_FIELD = "ameide_organization_name"
AMEIDE_USER_ID_FIELD = "ameide_user_id"
AMEIDE_IDEMPOTENCY_KEY_FIELD = "ameide_idempotency_key"
AMEIDE_EMPLOYEE_FIELDS = (
	AMEIDE_ORGANIZATION_ID_FIELD,
	AMEIDE_ORGANIZATION_NAME_FIELD,
	AMEIDE_USER_ID_FIELD,
	AMEIDE_IDEMPOTENCY_KEY_FIELD,
)
DEFAULT_EMPLOYEE_GENDER = "Male"
DEFAULT_EMPLOYEE_BIRTH_DATE = "1970-01-01"
DEFAULT_EMPLOYEE_JOINING_DATE = "2026-01-01"
DB_CONCURRENCY_RETRY_ATTEMPTS = 3
DB_CONCURRENCY_RETRY_BASE_SECONDS = 0.1
DB_CONCURRENCY_RETRY_MAX_SECONDS = 0.5
TRANSIENT_DB_ERROR_NAMES = (
	"QueryDeadlockError",
	"DeadlockError",
	"LockWaitTimeoutError",
	"QueryTimeoutError",
)
TRANSIENT_DB_ERROR_MESSAGES = (
	"deadlock",
	"lock wait timeout",
	"try restarting transaction",
)


def _normalize_roles(roles: str | list[str] | tuple[str, ...]) -> list[str]:
	if isinstance(roles, str):
		roles = json.loads(roles)
	if not isinstance(roles, list | tuple):
		raise ValueError("roles must be a list")

	normalized = [str(role).strip() for role in roles if str(role).strip()]
	if not normalized:
		raise ValueError("roles must not be empty")
	return normalized


def _split_name(full_name: str) -> tuple[str, str]:
	parts = str(full_name or "").strip().split(" ", 1)
	first_name = parts[0] if parts and parts[0] else "Employee"
	last_name = parts[1] if len(parts) > 1 else ""
	return first_name, last_name


def _ensure_role(role_name: str) -> None:
	if frappe.db.exists("Role", role_name):
		frappe.db.set_value("Role", role_name, "desk_access", 0)
		return

	role = frappe.new_doc("Role")
	role.update({"role_name": role_name, "home_page": "", "desk_access": 0})
	role.save(ignore_permissions=True)


def _ensure_onboarding_role_contract() -> None:
	_ensure_role(ONBOARDING_SERVICE_ROLE)


def _require_onboarding_service_role() -> None:
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	if ONBOARDING_SERVICE_ROLE not in roles:
		raise PermissionError(f"{ONBOARDING_SERVICE_ROLE} role required")


def _require_text(value: str, field_name: str) -> str:
	value = str(value or "").strip()
	if not value:
		raise ValueError(f"{field_name} is required")
	return value


def _employee_display_name(email: str, full_name: str) -> str:
	full_name = str(full_name or "").strip()
	if full_name:
		return full_name
	local, _, _domain = str(email or "").partition("@")
	return local.strip() or "Ameide Employee"


def _configured_employee_company() -> str:
	values = [
		os.environ.get("AMEIDE_HR_EMPLOYEE_COMPANY"),
		_get_conf_value("ameide_hr_employee_company"),
		_get_conf_value("ameide_employee_company"),
		_get_global_default_company(),
		_get_global_defaults_company(),
		_get_first_company(),
	]
	for value in values:
		company = str(value or "").strip()
		if company and frappe.db.exists(COMPANY_DOCTYPE, company):
			return company
	raise ValueError("HR employee company is not configured")


def _get_conf_value(key: str) -> str:
	conf = getattr(frappe, "conf", None)
	if conf is None:
		return ""
	if hasattr(conf, "get"):
		return str(conf.get(key) or "")
	return str(getattr(conf, key, "") or "")


def _get_global_default_company() -> str:
	defaults = getattr(frappe, "defaults", None)
	get_global_default = getattr(defaults, "get_global_default", None)
	if not callable(get_global_default):
		return ""
	return str(get_global_default("company") or "")


def _get_global_defaults_company() -> str:
	get_single_value = getattr(frappe.db, "get_single_value", None)
	if not callable(get_single_value):
		return ""
	return str(get_single_value("Global Defaults", "default_company") or "")


def _get_first_company() -> str:
	get_all = getattr(frappe.db, "get_all", None) or getattr(frappe, "get_all", None)
	if not callable(get_all):
		return ""
	try:
		companies = get_all(COMPANY_DOCTYPE, pluck="name", limit=1, order_by="creation asc")
	except TypeError:
		companies = get_all(COMPANY_DOCTYPE, pluck="name", limit=1)
	if not companies:
		return ""
	return str(companies[0] or "")


def _is_transient_db_concurrency_error(error: Exception) -> bool:
	error_type = type(error)
	error_names = {error_type.__name__}
	if error_type.__module__:
		error_names.add(error_type.__module__)

	if any(name in error_name for error_name in error_names for name in TRANSIENT_DB_ERROR_NAMES):
		return True

	message = str(error).lower()
	if type(error).__name__ == "OperationalError":
		return any(marker in message for marker in TRANSIENT_DB_ERROR_MESSAGES)
	return any(marker in message for marker in TRANSIENT_DB_ERROR_MESSAGES)


def _rollback_after_transient_failure() -> None:
	rollback = getattr(frappe.db, "rollback", None)
	if callable(rollback):
		rollback()


def _sleep_before_retry(attempt: int) -> None:
	limit = min(DB_CONCURRENCY_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), DB_CONCURRENCY_RETRY_MAX_SECONDS)
	time.sleep(random.uniform(0, limit))


def _with_db_concurrency_retry(operation):
	for attempt in range(1, DB_CONCURRENCY_RETRY_ATTEMPTS + 1):
		try:
			return operation()
		except Exception as error:
			if not _is_transient_db_concurrency_error(error):
				raise

			_rollback_after_transient_failure()
			if attempt == DB_CONCURRENCY_RETRY_ATTEMPTS:
				raise
			_sleep_before_retry(attempt)

	raise RuntimeError("unreachable db concurrency retry state")


def _employee_fields(email: str, full_name: str, company: str) -> dict[str, object]:
	first_name, _last_name = _split_name(full_name)
	return {
		"first_name": first_name,
		"employee_name": _employee_display_name(email, full_name),
		"company": company,
		"company_email": email,
		"gender": DEFAULT_EMPLOYEE_GENDER,
		"date_of_birth": DEFAULT_EMPLOYEE_BIRTH_DATE,
		"date_of_joining": DEFAULT_EMPLOYEE_JOINING_DATE,
		"status": "Active",
	}


def _ameide_employee_fields(
	organization_name: str,
	organization_id: str,
	user_id: str,
	idempotency_key: str,
) -> dict[str, object]:
	_require_ameide_employee_fields()
	fields = {
		AMEIDE_ORGANIZATION_NAME_FIELD: str(organization_name or "").strip(),
		AMEIDE_ORGANIZATION_ID_FIELD: str(organization_id or "").strip(),
		AMEIDE_USER_ID_FIELD: str(user_id or "").strip(),
		AMEIDE_IDEMPOTENCY_KEY_FIELD: str(idempotency_key or "").strip(),
	}
	return {key: value for key, value in fields.items() if value}


def _require_ameide_employee_fields() -> None:
	missing = [field for field in AMEIDE_EMPLOYEE_FIELDS if not _employee_has_field(field)]
	if missing:
		raise RuntimeError(f"Employee is missing Ameide fields: {', '.join(missing)}")


def _employee_has_field(field_name: str) -> bool:
	get_meta = getattr(frappe, "get_meta", None)
	if not callable(get_meta):
		return False
	meta = get_meta(EMPLOYEE_DOCTYPE)
	has_field = getattr(meta, "has_field", None)
	if callable(has_field):
		return bool(has_field(field_name))
	return any(getattr(field, "fieldname", "") == field_name for field in getattr(meta, "fields", []))


def _find_employee_name(email: str, user_id: str = "") -> str:
	user_id = str(user_id or "").strip()
	if user_id:
		name = frappe.db.exists(EMPLOYEE_DOCTYPE, {AMEIDE_USER_ID_FIELD: user_id})
		if name:
			return name
	return frappe.db.exists(EMPLOYEE_DOCTYPE, {"company_email": email})


def _apply_fields(doc, fields: dict[str, object]) -> None:
	for key, value in fields.items():
		setattr(doc, key, value)


def _restore_fields(doc, fields: dict[str, object]) -> None:
	for key, value in fields.items():
		setattr(doc, key, value)


def _save_doc_with_fields(doc, fields: dict[str, object]) -> None:
	previous_fields = {key: getattr(doc, key, None) for key in fields}
	_apply_fields(doc, fields)
	try:
		doc.save(ignore_permissions=True)
	except Exception as error:
		if _is_transient_db_concurrency_error(error):
			_restore_fields(doc, previous_fields)
		raise


def _doc_matches_fields(doc, fields: dict[str, object]) -> bool:
	for key, value in fields.items():
		if getattr(doc, key, None) != value:
			return False
	return True


@frappe.whitelist(methods=["POST"])
def ensure_employee(
	email: str,
	full_name: str,
	organization_name: str,
	organization_id: str = "",
	user_id: str = "",
	idempotency_key: str = "",
) -> dict[str, object]:
	_require_onboarding_service_role()
	email = _require_text(email, "email")

	return _with_db_concurrency_retry(
		lambda: _ensure_employee_once(
			email, full_name, organization_name, organization_id, user_id, idempotency_key
		)
	)


def _ensure_employee_once(
	email: str,
	full_name: str,
	organization_name: str,
	organization_id: str = "",
	user_id: str = "",
	idempotency_key: str = "",
) -> dict[str, object]:
	organization_name = _require_text(organization_name, "organization_name")
	organization_id = _require_text(organization_id, "organization_id")
	user_id = _require_text(user_id, "user_id")
	company = _configured_employee_company()
	fields = {
		**_employee_fields(email, full_name, company),
		**_ameide_employee_fields(organization_name, organization_id, user_id, idempotency_key),
	}

	created = False
	name = _find_employee_name(email, user_id)
	if name:
		employee = frappe.get_doc(EMPLOYEE_DOCTYPE, name)
		if not _doc_matches_fields(employee, fields):
			_save_doc_with_fields(employee, fields)
	else:
		employee = frappe.get_doc({"doctype": EMPLOYEE_DOCTYPE, **fields})
		employee.insert(ignore_permissions=True)
		created = True

	return {
		"email": email,
		"name": employee.name,
		"employee": employee.name,
		"created": created,
		"organization_id": organization_id,
		"user_id": user_id,
		"idempotency_key": idempotency_key,
	}


@frappe.whitelist(methods=["POST"])
def disable_employee(
	email: str,
	full_name: str = "",
	organization_name: str = "",
	organization_id: str = "",
	user_id: str = "",
	idempotency_key: str = "",
) -> dict[str, object]:
	_require_onboarding_service_role()
	email = _require_text(email, "email")

	return _with_db_concurrency_retry(
		lambda: _disable_employee_once(
			email, full_name, organization_name, organization_id, user_id, idempotency_key
		)
	)


def _disable_employee_once(
	email: str,
	full_name: str = "",
	organization_name: str = "",
	organization_id: str = "",
	user_id: str = "",
	idempotency_key: str = "",
) -> dict[str, object]:
	name = frappe.db.exists(EMPLOYEE_DOCTYPE, {"company_email": email})
	if not name:
		return {
			"email": email,
			"removed": False,
			"organization_id": organization_id,
			"user_id": user_id,
			"idempotency_key": idempotency_key,
		}

	employee = frappe.get_doc(EMPLOYEE_DOCTYPE, name)
	removed = getattr(employee, "status", "") != "Inactive"
	if removed:
		previous_status = getattr(employee, "status", "")
		employee.status = "Inactive"
		try:
			employee.save(ignore_permissions=True)
		except Exception as error:
			if _is_transient_db_concurrency_error(error):
				employee.status = previous_status
			raise

	return {
		"email": email,
		"name": employee.name,
		"employee": employee.name,
		"removed": removed,
		"organization_id": organization_id,
		"user_id": user_id,
		"idempotency_key": idempotency_key,
	}


@frappe.whitelist(methods=["POST"])
def ensure_token(email: str, full_name: str, roles: str | list[str] | tuple[str, ...]) -> dict[str, str]:
	import frappe
	from frappe.core.doctype.user.user import generate_keys

	roles = _normalize_roles(roles)
	_ensure_onboarding_role_contract()
	if ONBOARDING_SERVICE_ROLE not in roles:
		roles.append(ONBOARDING_SERVICE_ROLE)
	parts = full_name.split(" ", 1)

	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": parts[0],
				"last_name": parts[1] if len(parts) > 1 else "",
				"enabled": 1,
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles],
			}
		)
		user.insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)
		user.enabled = 1
		user.user_type = "System User"
		existing_roles = {row.role for row in user.roles}
		for role in roles:
			if role not in existing_roles:
				user.append("roles", {"role": role})
		user.save(ignore_permissions=True)

	keys = generate_keys(email)
	user = frappe.get_doc("User", email)
	result = {
		"email": email,
		"api_key": user.api_key,
		"api_secret": keys.get("api_secret") if isinstance(keys, dict) else "",
	}
	return result
