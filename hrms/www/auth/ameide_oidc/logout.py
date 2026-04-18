from __future__ import annotations

from urllib.parse import urlencode

import frappe
from frappe.utils.oauth import build_oauth_url

from hrms.ameide_sso.provider import get_social_login_key_ref, resolve_social_login_key_name

no_cache = 1


def get_context(context=None):
	_post_logout_redirect = frappe.form_dict.get("post-logout-redirect") or "/hrms"
	post_logout_redirect_uri = frappe.utils.get_url(_post_logout_redirect)

	_logout_locally()

	provider = resolve_social_login_key_name()
	if not provider:
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = post_logout_redirect_uri
		return {}

	ref = get_social_login_key_ref(provider)
	if not ref.base_url or not ref.client_id:
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = post_logout_redirect_uri
		return {}

	end_session_endpoint = build_oauth_url(ref.base_url.rstrip("/"), "/protocol/openid-connect/logout")
	location = f"{end_session_endpoint}?{urlencode({'client_id': ref.client_id, 'post_logout_redirect_uri': post_logout_redirect_uri})}"

	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = location
	return {}


def _logout_locally() -> None:
	try:
		login_manager = getattr(frappe.local, "login_manager", None)
		if login_manager:
			login_manager.logout()
			# nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- local logout must persist session teardown before IdP redirect
			frappe.db.commit()
	except Exception:
		# best-effort: even if local logout fails, attempt IdP logout
		pass
