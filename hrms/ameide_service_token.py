import json

import frappe


def _normalize_roles(roles: str | list[str] | tuple[str, ...]) -> list[str]:
	if isinstance(roles, str):
		roles = json.loads(roles)
	if not isinstance(roles, list | tuple):
		raise ValueError("roles must be a list")

	normalized = [str(role).strip() for role in roles if str(role).strip()]
	if not normalized:
		raise ValueError("roles must not be empty")
	return normalized


@frappe.whitelist(methods=["POST"])
def ensure_token(email: str, full_name: str, roles: str | list[str] | tuple[str, ...]) -> dict[str, str]:
	import frappe
	from frappe.core.doctype.user.user import generate_keys

	roles = _normalize_roles(roles)
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
