from __future__ import annotations

from dataclasses import dataclass

import frappe


@dataclass(frozen=True)
class SocialLoginKeyRef:
	name: str
	base_url: str | None
	client_id: str | None


def resolve_social_login_key_name() -> str | None:
	configured = (
		frappe.conf.get("ameide_sso_provider")
		or frappe.conf.get("ameide_oidc_provider")
		or frappe.conf.get("ameide_keycloak_provider")
	)
	if configured and _is_enabled_social_login_key(configured):
		return configured

	for candidate in ("ameide", "keycloak", "Keycloak", "Ameide"):
		if _is_enabled_social_login_key(candidate):
			return candidate

	keycloak_named = frappe.get_all(
		"Social Login Key",
		filters={"enable_social_login": 1, "provider_name": ["like", "%Keycloak%"]},
		pluck="name",
		limit=1,
	)
	if keycloak_named:
		return keycloak_named[0]

	enabled = frappe.get_all(
		"Social Login Key",
		filters={"enable_social_login": 1},
		pluck="name",
		limit=2,
	)
	if len(enabled) == 1:
		return enabled[0]

	return None


def get_social_login_key_ref(name: str) -> SocialLoginKeyRef:
	row = frappe.get_value(
		"Social Login Key",
		name,
		["name", "base_url", "client_id"],
		as_dict=True,
	)
	if not row:
		raise frappe.DoesNotExistError(f"Social Login Key {name} not found")
	return SocialLoginKeyRef(name=row["name"], base_url=row.get("base_url"), client_id=row.get("client_id"))


def _is_enabled_social_login_key(name: str) -> bool:
	return bool(frappe.db.exists("Social Login Key", {"name": name, "enable_social_login": 1}))

