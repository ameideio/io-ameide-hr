from __future__ import annotations

import frappe
from frappe import _

from hrms.ameide_sso.provider import resolve_social_login_key_name
from hrms.ameide_sso.system_user_login import login_via_oauth2_as_system_user

no_cache = 1


def get_context(context=None):
	provider = resolve_social_login_key_name()
	if not provider:
		frappe.respond_as_web_page(
			_("SSO not configured"), _("Missing an enabled Social Login Key."), http_status_code=500
		)
		return {}

	code = frappe.form_dict.get("code")
	state = frappe.form_dict.get("state")
	if not (code and state):
		frappe.respond_as_web_page(_("Invalid Request"), _("Missing code or state"), http_status_code=417)
		return {}

	login_via_oauth2_as_system_user(provider=provider, code=code, state=state)
	return {}
