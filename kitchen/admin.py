from django.contrib import admin
from .models import Recipe, RecipeItem, ProductionBatch


class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    inlines = [RecipeItemInline]


@admin.register(ProductionBatch)
class ProductionBatchAdmin(admin.ModelAdmin):
    list_display = ("recipe", "quantity_produced", "produced_by", "is_executed", "created_at")
    readonly_fields = ("is_executed",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.produced_by = request.user
        super().save_model(request, obj, form, change)

        # Execute production ONLY once
        if not obj.is_executed:
            obj.execute()
