from hrms.www.auth.ameide_oidc import redirect as ameide_oidc_redirect


def get_context(context=None):
	return ameide_oidc_redirect.get_context(context)
