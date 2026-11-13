from django import forms
from django.forms import inlineformset_factory, modelform_factory

from .models import (SyncPeer, ApiToken, LandedCost, Location, Partner, Picking, PickingLine, Product, ProductCategory,
                     Scrap, UoM, UoMCategory, Warehouse, ZraConfig)


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "internal_ref", "barcode", "product_type", "category", "uom", "price", "secondary_price",
                  "cost", "min_qty", "max_qty", "tax_type", "zra_item_class_code", "zra_item_type", "image", "active"]


class PickingForm(forms.ModelForm):
    scheduled_date = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                                         input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"])

    class Meta:
        model = Picking
        fields = ["partner", "source", "dest", "scheduled_date", "origin", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["source"].queryset = Location.objects.filter(active=True)
        self.fields["dest"].queryset = Location.objects.filter(active=True)

    def clean(self):
        data = super().clean()
        if data.get("source") and data.get("source") == data.get("dest"):
            raise forms.ValidationError("Source and destination must be different locations.")
        return data


LineFormSet = inlineformset_factory(Picking, PickingLine, fields=["product", "qty", "unit_cost"], extra=3, can_delete=True)


class ScrapForm(forms.ModelForm):
    class Meta:
        model = Scrap
        fields = ["product", "location", "qty", "reason"]


class LandedCostForm(forms.ModelForm):
    class Meta:
        model = LandedCost
        fields = ["picking", "description", "amount", "date"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class ZraConfigForm(forms.ModelForm):
    class Meta:
        model = ZraConfig
        fields = ["enabled", "base_url", "tpin", "branch_id", "device_serial", "timeout_seconds",
                  "register_after_import", "push_stock_after_validate"]


class TokenForm(forms.ModelForm):
    class Meta:
        model = ApiToken
        fields = ["name"]


class ImportForm(forms.Form):
    MODES = [("create", "Create new products"), ("update", "Update existing products"), ("both", "Create / update products")]
    mode = forms.ChoiceField(choices=MODES, initial="create", widget=forms.RadioSelect)
    skip_errors = forms.BooleanField(required=False, initial=True, label="Skip rows with errors")
    register_zra = forms.BooleanField(required=False, label="Register with ZRA after import")
    file = forms.FileField(label="CSV or Excel (.xlsx) file")


def crud_form(model, fields):
    return modelform_factory(model, fields=fields)


CRUD = {
    "warehouses": (Warehouse, ["name", "code", "zra_branch_id"], "Warehouses"),
    "locations": (Location, ["name", "warehouse", "parent", "usage", "active"], "Locations"),
    "categories": (ProductCategory, ["name", "parent"], "Product categories"),
    "uom-categories": (UoMCategory, ["name"], "UoM categories"),
    "uoms": (UoM, ["name", "category", "ratio", "zra_qty_code", "zra_pkg_code"], "Units of measure"),
    "sync-peers": (SyncPeer, ["name", "url", "token", "active", "pull_products"], "Sync peers"),
    "partners": (Partner, ["name", "is_supplier", "is_customer", "phone", "email", "tpin"], "Contacts"),
}
