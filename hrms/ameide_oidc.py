from __future__ import annotations

import json
import os
from urllib.parse import quote, urlencode

import frappe
from frappe import _
from frappe.integrations.doctype.social_login_key.social_login_key import SocialLoginKey
from frappe.utils import get_url
from frappe.utils.oauth import (
	get_oauth2_authorize_url,
)

from hrms.ameide_sso.system_user_login import login_via_oauth2_as_system_user

PROVIDER_NAME = "keycloak"
APP_BASE_PATH = "/hrms"
CALLBACK_PATH = "/auth/ameide-oidc/redirect"
DEFAULT_SCOPE = "openid email profile"


def is_enabled() -> bool:
	return _env_flag("AMEIDE_OIDC_ENABLED")


def normalize_redirect_to(value: str | None, default: str = APP_BASE_PATH) -> str:
	if not value:
		return default

	value = str(value).strip()
	if not value:
		return default

	if "://" in value or value.startswith("//"):
		return default

	if not value.startswith("/"):
		value = f"/{value}"

	return value


def build_login_redirect_location(redirect_to: str | None = None) -> str:
	target = normalize_redirect_to(redirect_to)
	return f"/auth/ameide-oidc?redirect-to={quote(target)}"


def begin_login(redirect_to: str | None = None) -> None:
	ensure_social_login_key()
	frappe.local.flags.redirect_location = get_oauth2_authorize_url(
		PROVIDER_NAME,
		normalize_redirect_to(redirect_to),
	)
	raise frappe.Redirect


def complete_login(code: str, state: str) -> None:
	ensure_social_login_key()
	login_via_oauth2_as_system_user(provider=PROVIDER_NAME, code=code, state=state)


def build_logout_redirect_location(id_token_hint: str | None = None) -> str:
	endpoint = _env("AMEIDE_OIDC_END_SESSION_ENDPOINT") or f"{_issuer_url()}/protocol/openid-connect/logout"
	query = [
		("client_id", _client_id()),
		("post_logout_redirect_uri", _post_logout_redirect_uri()),
	]
	if id_token_hint:
		query.append(("id_token_hint", id_token_hint))

	return f"{endpoint}?{urlencode(query)}"


def ensure_social_login_key() -> None:
	if not is_enabled():
		frappe.throw(_("Ameide OIDC is not enabled"))

	doc = (
		frappe.get_doc("Social Login Key", PROVIDER_NAME)
		if frappe.db.exists("Social Login Key", PROVIDER_NAME)
		else None
	)
	if not doc:
		doc = frappe.new_doc("Social Login Key")
		SocialLoginKey.get_social_login_provider(doc, "Keycloak", initialize=True)

	desired_values = {
		"provider_name": "Keycloak",
		"social_login_provider": "Keycloak",
		"enable_social_login": 1,
		"custom_base_url": 1,
		"base_url": _issuer_url(),
		"client_id": _client_id(),
		"client_secret": _client_secret(),
		"redirect_url": CALLBACK_PATH,
		"authorize_url": "/protocol/openid-connect/auth",
		"access_token_url": "/protocol/openid-connect/token",
		"api_endpoint": "/protocol/openid-connect/userinfo",
		"auth_url_data": json.dumps({"response_type": "code", "scope": _scope()}),
		"user_id_property": "preferred_username",
		"sign_ups": "Allow",
	}

	changed = False
	for fieldname, value in desired_values.items():
		if doc.get(fieldname) != value:
			doc.set(fieldname, value)
			changed = True

	if not changed and not doc.is_new():
		return

	doc.flags.ignore_permissions = True
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def _env(name: str, default: str | None = None) -> str | None:
	return os.getenv(name, default)


def _env_flag(name: str, default: bool = False) -> bool:
	value = _env(name)
	if value is None:
		return default
	return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _issuer_url() -> str:
	return (_env("AMEIDE_OIDC_ISSUER_URL") or "").rstrip("/")


def _client_id() -> str:
	return _env("AMEIDE_OIDC_CLIENT_ID") or "io-ameide-hr"


def _client_secret() -> str:
	return _env("AMEIDE_OIDC_CLIENT_SECRET") or ""


def _scope() -> str:
	return _env("AMEIDE_OIDC_SCOPE") or DEFAULT_SCOPE


def _post_logout_redirect_uri() -> str:
	return _env("AMEIDE_OIDC_POST_LOGOUT_REDIRECT_URI") or get_url(APP_BASE_PATH)
