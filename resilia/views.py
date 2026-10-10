from re import sub
from urllib import request
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import Avg, Count
from django.db import transaction, IntegrityError
from django.conf import settings
from functools import wraps
from .models import AnxietyTrigger, JournalEntry, Subscription, OrganisationLead, CBTExercise, ExerciseCompletion
from .forms import AnxietyTriggerForm, JournalEntryForm, OrganisationContactForm
from django.core.mail import send_mail
from .utils import get_user_cbt_recommendations
from django.views.decorators.csrf import csrf_exempt
import stripe
import json
from .forms import UserRegisterForm
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from .forms import UserUpdateForm
from django.contrib import messages
from django.db.models import Max
from resilia.models import Affiliate
from .models import Affiliate
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
import re
from .utils import should_show_support_banner
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import (urlsafe_base64_encode, urlsafe_base64_decode,)
from django.utils.encoding import force_bytes
from .emails import (send_verification_email, add_user_to_brevo)
from django.contrib.auth import get_user_model
from .models import UserProfile, AffiliateCommission

User = get_user_model()

stripe.api_key = settings.STRIPE_SECRET_KEY

import re

def is_high_risk(text):
    if not text:
        return False

    text = text.lower().replace("’", "'").strip()

    # =========================
    # DIRECT HIGH-RISK PHRASES
    # =========================
    direct_phrases = [
        "suicid",
        "kill myself",
        "end my life",
        "want to die",
        "i want to die",
        "i feel like dying",
        "don't want to live",
        "dont want to live",
        "no reason to live",
        "better off dead",
        "life is not worth it",
        "i can't go on",
        "cant go on",
        "tired of living",
        "harm myself",
        "self harm",
    ]

    if any(p in text for p in direct_phrases):
        return True

    # =========================
    # PATTERN-BASED DETECTION
    # =========================
    patterns = [
        r"\bi feel like (giving up|i'm done|i am done)\b",
        r"\bi don't see (a point|any point|a future)\b",
        r"\beverything (feels pointless|is pointless)\b",
        r"\bi can't do this anymore\b",
        r"\bi am exhausted with life\b",
        r"\bi wish i wasn't here\b",
        r"\bi wish i could disappear\b",
        r"\bnothing matters anymore\b",
    ]

    if any(re.search(pattern, text) for pattern in patterns):
        return True

    # =========================
    # WEIGHTED SCORING SYSTEM
    # =========================
    risk_words = [
        "alone", "empty", "worthless", "hopeless",
        "tired", "numb", "broken", "done", "lost"
    ]

    score = sum(1 for word in risk_words if word in text)

    # If multiple emotional distress words → flag
    if score >= 3:
        return True

    return False

# =========================
# SUBMIT LEAD
# =========================
@csrf_exempt
def submit_lead(request):
    if request.method == "POST":
        data = json.loads(request.body)

        OrganisationLead.objects.create(
            organisation_name=data.get("organisation_name"),
            contact_name=data.get("contact_name"),
            phone=data.get("phone"),
            email=data.get("email"),
            role=data.get("role"),
            organisation_type=data.get("organisation_type"),
            organisation_size=data.get("organisation_size"),
            message=data.get("message"),
        )

        return JsonResponse({"success": True})

    return JsonResponse({"success": False})


def premium_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):

        if not request.user.is_authenticated:
            return redirect("resilia:login")

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        try:
            sub = Subscription.objects.get(user=request.user)
        except Subscription.DoesNotExist:
            return redirect("resilia:upgrade")

        if not sub.is_active and not sub.free_access and not sub.is_trial_active():
             return redirect("resilia:upgrade")

        return view_func(request, *args, **kwargs)

    return wrapper


# =========================
# CONTACT
# =========================
def contact(request):
    if request.method == "POST":

        phone = request.POST.get("phone", "").strip()
        message = request.POST.get("message", "").strip()

        # =========================
        # Logged-in user
        # =========================
        if request.user.is_authenticated:
            name = f"{request.user.first_name} {request.user.last_name}".strip()
            email = request.user.email

        # =========================
        # Guest user
        # =========================
        else:
            name = request.POST.get("name", "").strip()
            email = request.POST.get("email", "").strip()

            # Name validation
            if not name:
                messages.error(request, "Name is required.")
                return redirect("resilia:contact")

            # Email validation
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, "Please enter a valid email address.")
                return redirect("resilia:contact")

        # =========================
        # Phone validation (ALL users)
        # =========================
        if not phone:
            messages.error(request, "Phone number is required.")
            return redirect("resilia:contact")

        # Allow digits only (10–15 digits)
        if not re.fullmatch(r"\+?\d{10,15}", phone):
            messages.error(request, "Enter phone with country code (e.g. +447123456789).")
            return redirect("resilia:contact")

        # =========================
        # Save lead
        # =========================
        OrganisationLead.objects.create(
            contact_name=name,
            email=email,
            phone=phone,
            message=message,
        )

        # =========================
        # Send email notification
        # =========================
        send_mail(
            subject="New Coaching Request",
            message=f"""
New coaching request received:

Name: {name}
Email: {email}
Phone: {phone}

Message:
{message}
""",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.DEFAULT_FROM_EMAIL],
        )

        return redirect("resilia:contact_success")

    # =========================
    # GET request (prefill form)
    # =========================
    return render(request, "contact.html", {
        "prefilled_email": request.user.email if request.user.is_authenticated else "",
        "prefilled_name": f"{request.user.first_name} {request.user.last_name}".strip() if request.user.is_authenticated else "",
    })


def contact_success(request):
    return render(request, "contact_success.html")

# =========================
# HOME
# =========================


def home(request):
    affiliate = None

    if request.user.is_authenticated:
        try:
            affiliate = Affiliate.objects.get(user=request.user)
        except Affiliate.DoesNotExist:
            affiliate = None

    context = {
        "affiliate": affiliate,
        # keep your existing context variables here
    }

    # =========================
    # USER DATA
    # =========================
    if request.user.is_authenticated:
       triggers = AnxietyTrigger.objects.filter(user=request.user)
       journal_entries = JournalEntry.objects.filter(user=request.user)
    else:
       triggers = AnxietyTrigger.objects.none()
       journal_entries = JournalEntry.objects.none()

    today = timezone.now().date()
    last_7_days = [today - timedelta(days=i) for i in range(6, -1, -1)]

    journal_days = set(
        journal_entries.values_list("created_at__date", flat=True)
    )

    streak = 0
    for day in reversed(last_7_days):
        if day in journal_days:
            streak += 1
        else:
            break

    # =========================
    # SUBSCRIPTION
    # =========================
    is_active = False  # ✅ ALWAYS define first

    sub = None
    if request.user.is_authenticated:
       sub = Subscription.objects.filter(user=request.user).first()

    if sub:
       is_active = sub.is_active or sub.is_trial_active()

    # =========================
    # OPTIONAL CONTENT
    # =========================
    affirmation = None

    journal_count = journal_entries.count()
    has_entries = journal_count > 0

    if not has_entries:
        affirmation = "Start small. Even one thought written down is progress."

    elif triggers.filter(intensity__gte=8).exists():
        affirmation = "That sounds heavy. You don’t have to solve everything today."

    elif streak >= 5:
        affirmation = "You’re building something powerful. Keep going gently."

    elif streak == 0:
        affirmation = "It’s okay to restart. You haven’t lost your progress."

    elif journal_count < 3:
        affirmation = "You’re beginning to understand your patterns. Stay with it."

    else:
        affirmation = "You’re doing better than you think."

    max_intensity = triggers.aggregate(Max("intensity"))["intensity__max"] or 0

    if max_intensity >= 9:
        mood = "overwhelmed"
    elif max_intensity >= 7:
        mood = "high"
    elif max_intensity >= 4:
        mood = "moderate"
    else:
        mood = "low"

    completed_ids = []  # ✅ always exists

    if request.user.is_authenticated:
        completed_ids = list(
        ExerciseCompletion.objects.filter(
            user=request.user
        ).values_list("exercise_id", flat=True)
    )

    # Try to get NEW exercises first
    exercise = CBTExercise.objects.filter(
        mood_level=mood
    ).exclude(
        id__in=completed_ids
    ).order_by("?").first()

    # If all exercises done → allow repeats
    if not exercise:
        exercise = CBTExercise.objects.filter(
            mood_level=mood
        ).order_by("?").first()


# fallback if none found
    if not exercise:
        exercise = CBTExercise.objects.filter(
        mood_level="moderate"
    ).first()

    show_banner = should_show_support_banner(request)
    return render(
        request,
        "home.html",
        {
            "last_7_days": last_7_days,
            "journal_days": journal_days,
            "streak": streak,
            "affirmation": affirmation,
            "is_active": is_active,
            "exercise": exercise,
            "affiliate": affiliate,
            "has_access": is_active,
            "show_support_banner": show_banner,
        },
    )


@login_required
def complete_exercise(request, pk):
    if request.method == "POST":
        exercise = CBTExercise.objects.get(pk=pk)

        ExerciseCompletion.objects.get_or_create(
            user=request.user,
            exercise=exercise
        )

        exercise = CBTExercise.objects.first()

        return JsonResponse({"status": "success"})

    return JsonResponse({"status": "invalid"}, status=400)
# =========================
# STATIC PAGES
# =========================
def about(request):
    return render(request, "about.html")


def work_with_me(request):
    return render(request, "work_with_me.html")


def terms_of_use(request):
    return render(request, "terms_of_use.html")


def privacy_policy(request):
    return render(request, "privacy_policy.html")


# =========================
# AUTH
# =========================
def register(request):

    # STEP 3: capture ref
    ref_code = request.GET.get("ref")

    if ref_code:
        request.session["ref_code"] = ref_code

    if request.method == "POST":

        form = UserRegisterForm(request.POST)

        if form.is_valid():

            user = form.save(commit=False)

            user.first_name = form.cleaned_data.get('first_name')

            user.last_name = form.cleaned_data.get('last_name')

            # User inactive until email verified
            user.is_active = False

            user.save()

            # create subscription
            subscription = Subscription.objects.create(user=user)

            # STEP 4: attach referral
            ref_code = request.session.get("ref_code")

            if ref_code:

                from resilia.models import Affiliate

                try:
                    affiliate = Affiliate.objects.get(code=ref_code)

                    subscription.referred_by = affiliate

                    subscription.save()

                except Affiliate.DoesNotExist:
                    pass

            # =========================
            # EMAIL VERIFICATION
            # =========================

            token = default_token_generator.make_token(user)

            uid = urlsafe_base64_encode(force_bytes(user.pk))

            verification_link = (
                f"https://app.veylin.co.uk/verify/{uid}/{token}/"
            )
            print("EMAIL FUNCTION RUNNING")
            send_verification_email(user, verification_link)

            return redirect("resilia:verification_sent")

    else:

        form = UserRegisterForm()

    return render(request, "register.html", {"form": form})

def verification_sent(request):
    return render(
        request,
        "registration/verification_sent.html"
    )

def verify_email(request, uidb64, token):

    print("UIDB64:", uidb64)
    print("TOKEN:", token)

    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        print("DECODED UID:", uid)

        user = User.objects.get(pk=uid)
        print("USER:", user.username)

    except Exception as e:
        print("ERROR:", e)
        user = None

    if user:
        print(
            "TOKEN VALID:",
            default_token_generator.check_token(user, token)
        )

    if user and default_token_generator.check_token(user, token):

        user.is_active = True
        user.save()

        add_user_to_brevo(user)

        messages.success(
            request,
            "Your email has been verified successfully."
        )

        return redirect("resilia:login")

    messages.error(
        request,
        "Verification link is invalid or expired."
    )

    return redirect("resilia:register")



def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)

        if form.is_valid():
            user = form.get_user()
            print(f"LOGIN SUCCESS: {user.username}")

            login(request, user)
            return redirect("resilia:home")

        print("LOGIN FAILED")
        print(form.errors)

        messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()

    return render(request, "login.html", {"form": form})

def logout_view(request):
    logout(request)
    return redirect("resilia:home")


# =========================
# TRACKER
# =========================
@login_required
@premium_required
def tracker_list(request):
    triggers = AnxietyTrigger.objects.filter(user=request.user)
    exercises = get_user_cbt_recommendations(request.user)

    show_banner = should_show_support_banner(request)

    return render(
        request,
        "tracker_list.html",
        {
            "triggers": triggers,
            "exercises": exercises,
            "show_support_banner": show_banner,
        },
    )

def careers(request):
    return render(request, "careers.html")

def work_experience(request):
    return render(request, "work_experience.html")

def affiliate_info(request):
    return render(request, "affiliate_info.html")



@login_required
@premium_required
def tracker_create(request):
    if request.method == "POST":
        form = AnxietyTriggerForm(request.POST)

        if form.is_valid():
            trigger = form.save(commit=False)
            trigger.user = request.user
            trigger.save()
            profile = UserProfile.objects.get(user=request.user)

            profile.last_mood_entry = timezone.now()

            profile.save()

            text_to_check = " ".join([
                getattr(trigger, "situation", "") or "",
                getattr(trigger, "thought", "") or "",
                getattr(trigger, "emotion", "") or "",
                getattr(trigger, "behaviour", "") or "",
                getattr(trigger, "outcome", "") or "",
            ]).lower()

            # 🚨 HIGH RISK
            if is_high_risk(text_to_check):
                request.session["show_support_banner"] = True

                request.session["support_banner_time"] = timezone.now().isoformat()

            # 💬 HIGH INTENSITY
            elif trigger.intensity >= 8:
                messages.info(
                    request,
                    "That sounds really intense. You don’t have to solve anything right now. Just breathe."
                )

            return redirect("resilia:tracker_list")

    else:
        form = AnxietyTriggerForm()

    return render(request, "tracker_form.html", {"form": form})


@login_required
@premium_required
def tracker_update(request, pk):
    trigger = get_object_or_404(AnxietyTrigger, pk=pk, user=request.user)

    if request.method == "POST":
        form = AnxietyTriggerForm(request.POST, instance=trigger)

        if form.is_valid():

            if form.has_changed():
                trigger = form.save()

                text_to_check = " ".join([
                    trigger.situation or "",
                    trigger.thought or "",
                    trigger.emotion or "",
                    trigger.behaviour or "",
                    trigger.outcome or "",
                ]).lower()

                # 🚨 HIGH RISK
                if is_high_risk(text_to_check):
                    request.session["show_support_banner"] = True

                    if not request.session.get("support_banner_time"):
                        request.session["support_banner_time"] = timezone.now().isoformat()

                # 💬 HIGH INTENSITY
                elif trigger.intensity >= 8:
                    messages.info(
                        request,
                        "That sounds really intense. You don’t have to solve anything right now. Just breathe."
                    )

                # ✅ OPTIONAL: clear if no longer risky
                else:
                    request.session.pop("show_support_banner", None)
                    request.session.pop("support_banner_time", None)

            return redirect("resilia:tracker_list")

    else:
        form = AnxietyTriggerForm(instance=trigger)

    return render(request, "tracker_form.html", {
        "form": form,
        "update": True
    })

@login_required
@premium_required
def exercise_detail(request, pk):
    exercise = get_object_or_404(CBTExercise, pk=pk)
    return render(request, "exercise_detail.html", {"exercise": exercise})



@login_required
@premium_required
def journal_list(request):
    entries = JournalEntry.objects.filter(user=request.user)

    show_banner = should_show_support_banner(request)

    return render(
        request,
        "journal/list.html",
        {
            "entries": entries,
            "show_support_banner": show_banner,  # ✅ FIXED NAME
        }
    )


@login_required
@premium_required
def journal_create(request, trigger_id=None):
    trigger = None

    if trigger_id:
        trigger = get_object_or_404(
            AnxietyTrigger, pk=trigger_id, user=request.user
        )

    if request.method == "POST":
        form = JournalEntryForm(request.POST)

        if form.is_valid():
            entry = form.save(commit=False)
            entry.user = request.user

            if trigger:
                entry.trigger = trigger

            text = (entry.content or "").lower()

            entry.save()
            profile = UserProfile.objects.get(user=request.user)

            profile.last_journal_entry = timezone.now()

            profile.save()
            # 🚨 HIGH RISK
            if is_high_risk(text):
                request.session["show_support_banner"] = True

                if not request.session.get("support_banner_time"):
                    request.session["support_banner_time"] = timezone.now().isoformat()

            return redirect("resilia:journal_list")

    else:
        form = JournalEntryForm(
            initial={"trigger": trigger} if trigger else None,
            user=request.user
        )

    return render(
        request,
        "journal/form.html",
        {"form": form, "trigger": trigger},
    )


@login_required
@premium_required
def journal_edit(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, user=request.user)

    if request.method == "POST":
        form = JournalEntryForm(request.POST, instance=entry)

        if form.is_valid():

            if form.has_changed():
                entry = form.save(commit=False)

                text = (entry.content or "").lower()

                entry.save()

                # 🚨 HIGH RISK
                if is_high_risk(text):
                    request.session["show_support_banner"] = True

                    if not request.session.get("support_banner_time"):
                        request.session["support_banner_time"] = timezone.now().isoformat()

                # ✅ OPTIONAL: clear if safe
                else:
                    request.session.pop("show_support_banner", None)
                    request.session.pop("support_banner_time", None)

            return redirect("resilia:journal_list")

    else:
        form = JournalEntryForm(instance=entry)

    return render(request, "journal/form.html", {
        "form": form,
        "edit": True
    })

@login_required
@premium_required
def journal_delete(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk, user=request.user)

    if request.method == "POST":
        entry.delete()
        return redirect("resilia:journal_list")

    return render(request, "journal/confirm_delete.html", {
        "entry": entry
    })


@login_required
def customer_portal(request):
    try:
        sub = Subscription.objects.get(user=request.user)
    except Subscription.DoesNotExist:
        return redirect("resilia:upgrade")

    # ✅ If user has free access → no Stripe portal
    if sub.free_access:
        messages.info(request, "You have free access. No subscription to manage.")
        return redirect("resilia:home")

    # ✅ If user has Stripe customer → go to portal
    if sub.stripe_customer_id:
        domain_url = request.build_absolute_uri("/")

        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=domain_url,
        )

        return redirect(session.url)

    # ❌ Only if no Stripe customer → checkout
    return redirect("resilia:upgrade")

# =========================
# STRIPE
# =========================
@login_required
def create_checkout_session(request):
    domain_url = request.build_absolute_uri('/')

    if not request.user.email:
        messages.error(request, "Please add an email to continue.")
        return redirect("resilia:home")

    # ✅ Safe get/create
    sub, _ = Subscription.objects.get_or_create(user=request.user)

    # 🔒 Trial gate
    if sub.has_used_trial:
        trial_period_days = 0
    else:
        trial_period_days = 7

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        customer_email=request.user.email,
        billing_address_collection='required',
        allow_promotion_codes=True,
        line_items=[{
            "price": "price_1UNB5QFT8cf21M5WJ4b1aShp",
            "quantity": 1,
        }],
        subscription_data={
            "trial_period_days": trial_period_days,
            "metadata": {
                "user_id": str(request.user.id)
            }
        },
        success_url=domain_url + "success/?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=domain_url + "upgrade/",
        metadata={
            "user_id": request.user.id
        }
    )

    return redirect(session.url)



def upgrade(request):
    trial_available = False  # default

    if request.user.is_authenticated:
        try:
            sub = Subscription.objects.get(user=request.user)
            trial_available = not sub.has_used_trial
        except Subscription.DoesNotExist:
            trial_available = True

    return render(request, "upgrade.html", {
        "trial_available": trial_available
    })


def subscription_success(request):
    session_id = request.GET.get("session_id")

    if not session_id:
        return redirect("resilia:home")

    try:
        session = stripe.checkout.Session.retrieve(session_id)

        sub, _ = Subscription.objects.get_or_create(user=request.user)
        sub.is_active = True
        sub.stripe_customer_id = session.get("customer")
        sub.save()

        messages.success(request, "Your subscription is now active 🎉")

    except Exception as e:
        print("Stripe error:", e)
        messages.error(request, "Something went wrong.")

    return render(request, "subscription_success.html")



def subscription_cancel(request):
    return render(request, "subscription_cancel.html")

@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except Exception:
        return HttpResponse(status=400)

    event_type = event["type"]
    data = event["data"]["object"]

    print("Stripe event:", event_type)

    # =========================
    # CHECKOUT COMPLETED
    # =========================
    if event_type == "checkout.session.completed":
        user_id = data["metadata"].get("user_id")
        customer_id = data["customer"]
        subscription_id = data.get("subscription")

        try:
            sub = Subscription.objects.get(user_id=user_id)

            sub.stripe_customer_id = customer_id
            sub.stripe_subscription_id = subscription_id
            sub.is_active = True

            # 🔒 Mark trial as used immediately
            if not sub.has_used_trial:
                sub.has_used_trial = True
                sub.trial_start = timezone.now()

            sub.save()

            print("✅ Checkout completed:", user_id)

        except Subscription.DoesNotExist:
            print("⚠️ Subscription not found:", user_id)


    # =========================
    # PAYMENT SUCCEEDED
    # =========================

    elif event_type == "invoice.payment_succeeded":
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")

        # Support newer Stripe invoice structures.
        parent = data.get("parent") or {}
        subscription_details = (
            parent.get("subscription_details") or {}
        )

        if not subscription_id:
            subscription_id = subscription_details.get("subscription")

        try:
            sub = Subscription.objects.get(
                stripe_customer_id=customer_id
            )

        except Subscription.DoesNotExist:
            # Recover the user ID from the Stripe Subscription itself.
            if not subscription_id:
                print("Cannot recover subscription: missing subscription ID.")
                return HttpResponse(status=500)

            try:
                stripe_sub = stripe.Subscription.retrieve(subscription_id)
            except stripe.StripeError as exc:
                print("Stripe subscription lookup failed:", exc)
                return HttpResponse(status=500)

            metadata = stripe_sub.get("metadata") or {}
            user_id = metadata.get("user_id")

            if not user_id:
                print(
                    "Stripe Subscription has no user_id metadata:",
                    subscription_id,
                )
                return HttpResponse(status=500)

            try:
                sub = Subscription.objects.get(user_id=int(user_id))
            except (Subscription.DoesNotExist, ValueError, TypeError):
                print("Local subscription not found for user:", user_id)
                return HttpResponse(status=500)

            sub.stripe_customer_id = customer_id


        sub.is_active = True
        if subscription_id:
            sub.stripe_subscription_id = subscription_id
        sub.save()

        print("Payment succeeded:", customer_id)

        amount_paid = int(data.get("amount_paid") or 0)
        invoice_id = data.get("id")
        currency = (data.get("currency") or "").lower()

        # Only consider positive GBP payments for a subscription.
        if (
            amount_paid > 0
            and invoice_id
            and currency == "gbp"
            and subscription_id
        ):
            affiliate = sub.referred_by

            if not affiliate or affiliate.user_id == sub.user_id:
                print("No commission: missing affiliate or self-referral.")
            else:
                from decimal import Decimal

                try:
                    paid_invoices = [
                        invoice
                        for invoice in stripe.Invoice.list(
                            subscription=subscription_id,
                            status="paid",
                            limit=100,
                        ).auto_paging_iter()
                        if int(invoice.get("amount_paid") or 0) > 0
                        and (invoice.get("currency") or "").lower() == "gbp"
                    ]
                except stripe.StripeError as exc:
                    print("Stripe invoice lookup failed:", exc)
                    return HttpResponse(status=500)


                # Stripe may briefly not show the current invoice in
                # the paid-invoice list. Return 500 so Stripe can retry.
                if not any(
                    invoice.get("id") == invoice_id
                    for invoice in paid_invoices
                ):
                    print(
                        "Current paid invoice not found in Stripe invoice "
                        "list; requesting webhook retry:",
                        invoice_id,
                    )
                    return HttpResponse(status=500)

                first_paid_invoice = min(
                    paid_invoices,
                    key=lambda invoice: (
                        (invoice.get("status_transitions") or {}).get("paid_at")
                        or invoice.get("created", 0),
                        invoice.get("created", 0),
                        invoice.get("id", ""),
                    ),
                )


                if first_paid_invoice and first_paid_invoice.get("id") == invoice_id:
                    payment_amount = Decimal(amount_paid) / Decimal("100")
                    commission_amount = (
                        payment_amount * Decimal("0.20")
                    ).quantize(Decimal("0.01"))

                    try:
                        with transaction.atomic():
                            AffiliateCommission.objects.create(
                                affiliate=affiliate,
                                referred_subscription=sub,
                                stripe_invoice_id=invoice_id,
                                payment_amount=payment_amount,
                                commission_amount=commission_amount,
                                status="earned",
                            )
                        print(
                            "Affiliate commission recorded:",
                            affiliate.code,
                            commission_amount,
                        )
                    except IntegrityError:
                        print(
                            "Commission already recorded for this "
                            "subscription or invoice."
                        )
                else:
                    print(
                        "No commission: this is not the first "
                        "positive paid GBP invoice."
                    )
        else:
            print(
                "No commission: no positive GBP payment or "
                "missing invoice/subscription ID."
            )


    # =========================
    # SUBSCRIPTION CANCELLED
    # =========================
    elif event_type in ["customer.subscription.deleted", "invoice.payment_failed"]:
        customer_id = data.get("customer")

        try:
            sub = Subscription.objects.get(stripe_customer_id=customer_id)
            sub.is_active = False
            sub.save()

            print("❌ Subscription deactivated:", customer_id)

        except Subscription.DoesNotExist:
            print("⚠️ Subscription not found:", customer_id)

    return HttpResponse(status=200)

@login_required
def account(request):
    sub = Subscription.objects.filter(user=request.user).first()

    # PLAN LOGIC (keep what we built)
    plan = "Free"

    if sub:
        if sub.free_access:
            plan = "Free Access"
        elif sub.is_active and sub.stripe_subscription_id:
            plan = "Premium"
        elif not sub.has_used_trial:
            plan = "Trial Available"
        else:
            plan = "Free"

    # 👉 FORM HANDLING
    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your details have been updated ✅")
            return redirect("resilia:account")
    else:
        form = UserUpdateForm(instance=request.user)

    return render(request, "account.html", {
    "plan": plan,
    "sub": sub,
    "form": form,
    "is_active": sub.is_active if sub else False
})

@login_required
def complete_exercise(request, pk):
    exercise = CBTExercise.objects.get(pk=pk)

    ExerciseCompletion.objects.get_or_create(
        user=request.user,
        exercise=exercise
    )

    return JsonResponse({"status": "success"})

from django.http import JsonResponse

def clear_support_banner(request):
    request.session.pop("show_support_banner", None)
    request.session.pop("support_banner_time", None)
    return JsonResponse({"status": "ok"})
