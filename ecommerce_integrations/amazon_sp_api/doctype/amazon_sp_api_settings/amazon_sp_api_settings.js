// Copyright (c) 2022, Frappe and contributors
// For license information, please see license.txt

const MARKETPLACES = {
	CA: "A2EUQ1WTGCTBG2",
	US: "ATVPDKIKX0DER",
	MX: "A1AM78C64UM0Y8",
	BR: "A2Q3Y263D00KWC",
	ES: "A1RKKUPIHCS9HS",
	GB: "A1F83G8C2ARO7P",
	FR: "A13V1IB3VIYZZH",
	NL: "A1805IZSGTT6HS",
	DE: "A1PA6795UKMFR9",
	IT: "APJ6JRA9NG5V4",
	SE: "A2NODRKZP88ZB9",
	PL: "A1C3SOZRARQ6R3",
	EG: "ARBP9OOSHTCHU",
	TR: "A33AVAJ2PDY3EV",
	SA: "A17E79C6D8DWNP",
	AE: "A2VIGQ35RCS4UG",
	IN: "A21TJRUUN4KGV",
	SG: "A19VAU5U5O7RUS",
	AU: "A39IBJ37TRP1C6",
	JP: "A1VC38T7YXB528",
}

frappe.ui.form.on("Amazon SP API Settings", {
	country: (frm) => {
		frm.set_value("marketplace_id", MARKETPLACES[frm.doc.country]);
	}
});
