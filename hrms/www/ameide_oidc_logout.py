from hrms.www.auth.ameide_oidc import logout as ameide_oidc_logout


def get_context(context=None):
	return ameide_oidc_logout.get_context(context)
