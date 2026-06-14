from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from hrms.setup import get_custom_fields


def execute():
	create_custom_fields({"Employee": get_custom_fields()["Employee"]}, ignore_validate=True)
