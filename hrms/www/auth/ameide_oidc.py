from hrms.www.auth.ameide_oidc import index as ameide_oidc_index


def get_context(context=None):
	return ameide_oidc_index.get_context(context)
