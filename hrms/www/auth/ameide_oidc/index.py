from __future__ import annotations

import frappe
from frappe import _
from frappe.utils.oauth import get_oauth2_authorize_url

from hrms.ameide_sso.provider import resolve_social_login_key_name

no_cache = 1


def get_context(context=None):
	provider = resolve_social_login_key_name()
	if not provider:
		frappe.respond_as_web_page(
			_("SSO not configured"),
			_(
				"Missing an enabled Social Login Key for Ameide SSO. "
				"Set up a Social Login Key (Keycloak/OIDC) and enable it."
			),
			http_status_code=500,
		)
		return {}

	redirect_to = frappe.form_dict.get("redirect-to") or frappe.form_dict.get("redirect_to") or "/hrms"

	location = get_oauth2_authorize_url(provider, redirect_to)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = location
	return {}
