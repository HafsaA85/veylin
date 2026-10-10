from urllib import response

from django.contrib import admin
from .models import AnxietyTrigger, CBTExercise, JournalEntry, DailyPrompt
from .models import OrganisationLead
import csv
from django.http import HttpResponse
from .models import CBTExercise
from .models import AccessCode
from .models import Subscription
from .models import Affiliate
from .models import UserProfile

admin.site.register(UserProfile)



@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "is_active", "trial_start", "stripe_customer_id", "stripe_subscription_id")


@admin.register(AccessCode)
class AccessCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "is_active", "max_uses", "used_count")


@admin.register(AnxietyTrigger)
class AnxietyTriggerAdmin(admin.ModelAdmin):
    list_display = ("user", "situation", "intensity", "created_at")
    list_filter = ("intensity", "created_at")
    search_fields = ("situation", "user__username")
    ordering = ("-created_at",)


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "trigger")
    list_filter = ("created_at",)
    search_fields = ("content", "user__username")
    ordering = ("-created_at",)


@admin.register(DailyPrompt)
class DailyPromptAdmin(admin.ModelAdmin):
    list_display = ("text", "theme", "active")
    list_filter = ("theme", "active")
    search_fields = ("text",)


@admin.register(OrganisationLead)
class OrganisationLeadAdmin(admin.ModelAdmin):
    list_display = (
        "organisation_name",
        "contact_name",
        "email",
        "organisation_type",
        "organisation_size",
        "city",
        "country",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "organisation_type",
        "city",
        "created_at",
    )

    search_fields = (
        "organisation_name",
        "contact_name",
        "email",
    )

    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

    fieldsets = (
        ("Organisation", {
            "fields": (
                "organisation_name",
                "organisation_type",
                "organisation_size",
                "city",
                "country",
            )
        }),
        ("Contact", {
            "fields": (
                "contact_name",
                "email",
                "role",
            )
        }),
        ("Enquiry", {
            "fields": (
                "message",
                "notes",
                "status",
            )
        }),
        ("Meta", {
            "fields": ("created_at",),
        }),
    )

    actions = [
        "export_csv",
        "mark_contacted",
        "mark_demo",
        "mark_converted",
    ]

    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=resilia_leads.csv"

        writer = csv.writer(response)
        writer.writerow([
            "Organisation",
            "Contact",
            "Email",
            "Type",
            "Size",
            "Status",
            "Created",
        ])

        for lead in queryset:
            writer.writerow([
                lead.organisation_name,
                lead.contact_name,
                lead.email,
                lead.organisation_type,
                lead.organisation_size,
                lead.status,
                lead.created_at,
            ])

        return response

    export_csv.short_description = "Export selected leads to CSV"

    def mark_contacted(self, request, queryset):
        queryset.update(status="contacted")
    mark_contacted.short_description = "Mark as Contacted"

    def mark_demo(self, request, queryset):
        queryset.update(status="demo")
    mark_demo.short_description = "Mark as Demo Scheduled"

    def mark_converted(self, request, queryset):
        queryset.update(status="converted")
    mark_converted.short_description = "Mark as Converted"


# ✅ FIXED: CBTExerciseAdmin moved OUTSIDE
@admin.register(CBTExercise)
class CBTExerciseAdmin(admin.ModelAdmin):
    list_display = ("title", "mood_level")
    list_filter = ("mood_level",)
    search_fields = ("title", "description")


@admin.register(Affiliate)
class AffiliateAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "active_users", "monthly_payout_display")
    actions = ["export_affiliate_payouts"]

    def active_users(self, obj):
        return obj.active_users_count()
    active_users.short_description = "Active Users"

    def monthly_payout_display(self, obj):
        return f"£{obj.monthly_payout():.2f}"
    monthly_payout_display.short_description = "Total Earned Commission"

    # ✅ FIXED: proper indentation + writer usage
    def export_affiliate_payouts(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=affiliate_payouts.csv"

        writer = csv.writer(response)
        writer.writerow(["Code", "User", "Active Users", "Monthly Payout (£)"])

        for obj in queryset:
            writer.writerow([
                obj.code,
                obj.user.username if obj.user else "N/A",
                obj.active_users_count(),
                f"{obj.monthly_payout():.2f}"
            ])

        return response

    export_affiliate_payouts.short_description = "Export selected affiliates payouts"