import frappe
from frappe.boot import load_translations

from hrms.ameide_sso.provider import resolve_social_login_key_name

no_cache = 1


def get_context(context):
	csrf_token = frappe.sessions.get_csrf_token()
	# nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- csrf token issuance must persist before boot payload is returned
	frappe.db.commit()
	context = frappe._dict()
	context.csrf_token = csrf_token
	context.boot = get_boot()
	return context


@frappe.whitelist(methods=["POST"], allow_guest=True)
def get_context_for_dev():
	if not frappe.conf.developer_mode:
		frappe.throw(frappe._("This method is only meant for developer mode"))
	return get_boot()


def get_boot():
	bootinfo = frappe._dict(
		{
			"site_name": frappe.local.site,
			"push_relay_server_url": frappe.conf.get("push_relay_server_url") or "",
			"default_route": get_default_route(),
			"ameide_sso": _get_ameide_sso_boot(),
		}
	)

	bootinfo.lang = frappe.local.lang
	load_translations(bootinfo)

	return bootinfo


def get_default_route():
	return "/hrms"


def _get_ameide_sso_boot():
	provider = resolve_social_login_key_name()
	forced = bool(frappe.conf.get("ameide_sso_forced")) and bool(provider)
	return frappe._dict(
		{
			"forced": forced,
			"provider": provider,
			"login_url": "/auth/ameide-oidc?redirect-to=/hrms",
			"logout_url": "/auth/ameide-oidc/logout?post-logout-redirect=/hrms",
		}
	)
