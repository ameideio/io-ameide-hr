from __future__ import annotations

import os
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils.password import set_encrypted_password


@dataclass(frozen=True)
class SocialLoginKeyConfig:
	name: str
	provider_name: str
	base_url: str
	client_id: str
	client_secret: str
	redirect_url: str
	user_id_property: str


def ensure_social_login_key_from_env() -> dict[str, str | bool]:
	config = _config_from_env()
	return ensure_social_login_key(config)


def ensure_social_login_key(config: SocialLoginKeyConfig) -> dict[str, str | bool]:
	exists = bool(frappe.db.exists("Social Login Key", config.name))
	doc = (
		frappe.get_doc("Social Login Key", config.name)
		if exists
		else frappe.get_doc({"doctype": "Social Login Key", "name": config.name})
	)

	doc.provider_name = config.provider_name
	doc.client_id = config.client_id
	doc.base_url = config.base_url.rstrip("/")
	doc.custom_base_url = 1
	doc.authorize_url = "/protocol/openid-connect/auth"
	doc.access_token_url = "/protocol/openid-connect/token"
	doc.api_endpoint = "/protocol/openid-connect/userinfo"
	doc.redirect_url = config.redirect_url
	doc.user_id_property = config.user_id_property
	# Frappe validates that enabled providers already have an encrypted client
	# secret, so create/update the record disabled first and enable it after the
	# secret has been written.
	doc.enable_social_login = 0

	if exists:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)

	set_encrypted_password("Social Login Key", doc.name, "client_secret", config.client_secret)
	doc.reload()
	doc.enable_social_login = 1
	doc.save(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: bootstrap must persist the encrypted secret before bench exits
	return {"name": doc.name, "client_id": config.client_id, "updated": exists}


def _config_from_env() -> SocialLoginKeyConfig:
	return SocialLoginKeyConfig(
		name=_required_env("AMEIDE_OIDC_PROVIDER_NAME", default="ameide"),
		provider_name=_required_env("AMEIDE_OIDC_PROVIDER_LABEL", default="Ameide"),
		base_url=_required_env("AMEIDE_OIDC_ISSUER_URL"),
		client_id=_required_env("AMEIDE_OIDC_CLIENT_ID"),
		client_secret=_required_env("AMEIDE_OIDC_CLIENT_SECRET"),
		redirect_url=_required_env("AMEIDE_OIDC_REDIRECT_PATH", default="/auth/ameide-oidc/redirect"),
		user_id_property=_required_env("AMEIDE_OIDC_USER_ID_PROPERTY", default="sub"),
	)


def _required_env(name: str, default: str | None = None) -> str:
	value = os.environ.get(name, default)
	if value:
		return value
	frappe.throw(_("Missing required environment variable: {0}").format(name))
