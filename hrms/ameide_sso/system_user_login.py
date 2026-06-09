from __future__ import annotations

import base64
import json
from typing import Any

import frappe
from frappe import _
from frappe.utils.oauth import (
	get_email,
	get_info_via_oauth,
	redirect_post_login,
	update_oauth_user,
)


def login_via_oauth2_as_system_user(*, provider: str, code: str, state: str) -> None:
	state_dict = _decode_state(state)

	if not (state_dict and state_dict.get("token")):
		frappe.respond_as_web_page(_("Invalid Request"), _("Token is missing"), http_status_code=417)
		return

	info = get_info_via_oauth(provider, code, decoder=json.loads)
	user = (get_email(info) or "").lower()

	if not user:
		frappe.respond_as_web_page(
			_("Invalid Request"), _("Please ensure that your profile has an email address")
		)
		return

	if update_oauth_user(user, info, provider) is False:
		return

	_user = frappe.get_doc("User", user)
	_user.flags.ignore_permissions = True
	_user.flags.no_welcome_mail = True

	_user.append_roles("HR User")
	_user.user_type = "System User"
	_user.save()

	frappe.local.login_manager.login_as(user)  # because of a GET request! (matches upstream)
	frappe.db.commit()  # nosemgrep: login state must be flushed before redirecting from this GET handler

	redirect_post_login(
		desk_user=True,
		redirect_to=state_dict.get("redirect_to"),
		provider=provider,
	)


def _decode_state(state: str | dict[str, Any]) -> dict[str, Any] | None:
	if isinstance(state, dict):
		return state

	try:
		decoded = base64.b64decode(state)
		return json.loads(decoded.decode("utf-8"))
	except Exception:
		return None
