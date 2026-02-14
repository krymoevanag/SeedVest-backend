from django.contrib import admin
from .models import Contribution, Penalty

@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "group",
        "amount",
        "status",
        "due_date",
        "paid_date",
        "penalty",
    )
    list_filter = ("status", "group")
    search_fields = ("user__username", "user__email")


@admin.register(Penalty)
class PenaltyAdmin(admin.ModelAdmin):
    list_display = ("contribution", "amount", "reason", "created_at")
