from datetime import timedelta
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import secrets
from django.db.models.signals import post_save
from django.dispatch import receiver




# =========================
# Subscription
# =========================
class Subscription(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=False)

    # ✅ ADD THIS
    referred_by = models.ForeignKey(
        'Affiliate',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    free_access = models.BooleanField(default=False)
    trial_start = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    has_used_trial = models.BooleanField(default=False)

    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, null=True)

    def is_trial_active(self):
        if not self.trial_start:
            return False
        return timezone.now() <= self.trial_start + timedelta(days=7)

    def __str__(self):
        return f"{self.user.username} Subscription"


# =========================
# Anxiety Trigger (CBT core)
# =========================
class AnxietyTrigger(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="triggers")
    title = models.CharField(max_length=200)
    situation = models.TextField(help_text="What happened? Where were you?")
    thoughts = models.TextField(blank=True, help_text="What thoughts came to your mind?")
    feelings = models.TextField(blank=True, help_text="How did you feel in your body?")
    intensity = models.IntegerField(help_text="Rate anxiety 1–10")
    behaviour = models.TextField(blank=True, help_text="What did you do or avoid?")
    outcome = models.TextField(blank=True, help_text="What was the result or consequence?")
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        db_table = "noorforher_anxietytrigger"  # keep existing table

    def __str__(self):
        return f"{self.title} ({self.date})"


# =========================
# Journal Entry
# =========================
class JournalEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="journal_entries")
    trigger = models.ForeignKey(
        AnxietyTrigger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journal_entries"
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "noorforher_journalentry"  # keep existing table

    def __str__(self):
        return self.title


# =========================
# Daily Prompt
# =========================
class DailyPrompt(models.Model):
    PROMPT_THEMES = [
        ("journal", "Free Journal"),
        ("reflection", "Trigger Reflection"),
    ]

    text = models.CharField(max_length=300)
    theme = models.CharField(max_length=20, choices=PROMPT_THEMES, default="journal")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "noorforher_dailyprompt"

    def __str__(self):
        return f"{self.theme}: {self.text}"


# =========================
# Organisation Lead (B2B sales)
# =========================
class OrganisationLead(models.Model):

    STATUS_CHOICES = [
        ("new", "New"),
        ("contacted", "Contacted"),
        ("demo", "Demo Scheduled"),
        ("converted", "Converted"),
        ("closed", "Closed"),
    ]

    organisation_name = models.CharField(max_length=150)
    contact_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField()

    role = models.CharField(max_length=100, blank=True)
    organisation_type = models.CharField(max_length=100, blank=True)
    organisation_size = models.CharField(max_length=50, blank=True)
    phase = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default="UK")

    message = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="new"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.organisation_name} — {self.contact_name}"


# =========================
# CBT Exercise Library
# =========================
class CBTExercise(models.Model):
    MOOD_CHOICES = [
        ("low", "Low anxiety"),
        ("moderate", "Moderate anxiety"),
        ("high", "High anxiety"),
        ("overwhelmed", "Overwhelmed"),
    ]

    title = models.CharField(max_length=255, unique=True)
    description = models.TextField()
    instructions = models.TextField()
    mood_level = models.CharField(max_length=20, choices=MOOD_CHOICES)
    category = models.CharField(max_length=100, default="general")
    duration = models.IntegerField(default=2)  # in minutes
    instructions = models.TextField(blank=True)

    class Meta:
        db_table = "resilia_cbtexercise"

    def __str__(self):
        return self.title

# =========================
# Free Access Code
# =========================

class AccessCode(models.Model):
    code = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)
    max_uses = models.IntegerField(default=100)
    used_count = models.IntegerField(default=0)

    def __str__(self):
        return self.code

class ExerciseCompletion(models.Model):
     user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
     exercise = models.ForeignKey("CBTExercise", on_delete=models.CASCADE)
     completed_at = models.DateTimeField(auto_now_add=True)

class UserProfile(models.Model):

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
    )

    last_mood_entry = models.DateTimeField(
        null=True,
        blank=True
    )

    last_journal_entry = models.DateTimeField(
        null=True,
        blank=True
    )

    def __str__(self):
        return self.user.username


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):

    if created:

        UserProfile.objects.create(user=instance)

def generate_code():
    return secrets.token_urlsafe(5)

class Affiliate(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        null=True,   # keep this temporarily
        blank=True
    )
    code = models.CharField(max_length=50, unique=True, default=generate_code)

    def __str__(self):
        return self.code

    # ✅ ADD THIS
    def active_users_count(self):
        from resilia.models import Subscription
        return Subscription.objects.filter(
            referred_by=self,
            is_active=True
        ).count()

    def monthly_payout(self):
        from django.db.models import Sum

        total = self.commissions.filter(
            status="earned"
        ).aggregate(
            total=Sum("commission_amount")
        )["total"]

        return total or 0


@receiver(post_save, sender=User)
def create_affiliate_for_user(sender, instance, created, **kwargs):
    if created:
        Affiliate.objects.create(
            user=instance
        )


class AffiliateCommission(models.Model):
    affiliate = models.ForeignKey(
        'Affiliate',
        on_delete=models.PROTECT,
        related_name='commissions'
    )
    referred_subscription = models.ForeignKey(
        'Subscription',
        on_delete=models.PROTECT,
        related_name='affiliate_commissions'
    )
    stripe_invoice_id = models.CharField(
        max_length=255,
        unique=True
    )
    payment_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )
    commission_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )
    status = models.CharField(
        max_length=20,
        default='earned'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['referred_subscription'],
                name='unique_affiliate_commission_per_subscription'
            )
        ]

    def __str__(self):
        return (
            f"{self.affiliate.code} - "
            f"£{self.commission_amount} commission"
        )
