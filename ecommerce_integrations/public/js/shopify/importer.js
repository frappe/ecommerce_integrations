frappe.provide('shopify');

shopify.Importer = class {
    constructor(wrapper) {
        this.wrapper = $(wrapper).find('.layout-main-section');
        this.page = wrapper.page;
        this.init();
    }

    init() {
        console.log('Initializing Shopify Importer...');
    }
}
