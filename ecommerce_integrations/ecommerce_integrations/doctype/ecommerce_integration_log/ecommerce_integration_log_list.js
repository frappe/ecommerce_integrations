frappe.listview_settings["Ecommerce Integration Log"] = {
	hide_name_column: true,
	add_fields: ["status"],
	get_indicator: function(doc) {
		if(doc.status==="Success"){
			return [__("Success"), "green", "status,=,Success"];
        } else if(doc.status ==="Error"){
			return [__("Error"), "red", "status,=,Error"];
        } else if(doc.status ==="Queued"){
			return [__("Queued"), "orange", "status,=,Queued"];
        }
	}
}
