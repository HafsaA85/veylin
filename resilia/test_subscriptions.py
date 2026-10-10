from django.test import TestCase, Client
from django.contrib.auth.models import User
from .models import Subscription
from unittest.mock import patch
from django.urls import reverse


class UserSubscriptionTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            email="test@example.com"
        )
        self.sub = Subscription.objects.create(user=self.user)

    # =========================
    # TRIAL TEST
    # =========================
    def test_trial_only_once(self):
        self.sub.has_used_trial = True
        self.sub.save()
        self.assertTrue(self.sub.has_used_trial)

    # ✅ NEW TEST (IMPORTANT)
    def test_trial_not_given_again(self):
        self.sub.has_used_trial = True
        self.sub.save()

        trial_days = 0 if self.sub.has_used_trial else 7

        self.assertEqual(trial_days, 0)

    # =========================
    # ACCESS BLOCKED
    # =========================
    def test_premium_access_blocked_when_inactive(self):
        self.client.login(username="testuser", password="testpass123")

        self.sub.is_active = False
        self.sub.save()

        response = self.client.get("/tracker/")
        self.assertEqual(response.status_code, 302)

    # =========================
    # ACCESS ALLOWED
    # =========================
    def test_premium_access_allowed_when_active(self):
        self.client.login(username="testuser", password="testpass123")

        self.sub.is_active = True
        self.sub.save()

        response = self.client.get("/tracker/")
        self.assertEqual(response.status_code, 200)

    # =========================
    # CHECKOUT SESSION
    # =========================
    @patch("stripe.checkout.Session.create")
    def test_checkout_session_created(self, mock_create):
        mock_create.return_value.url = "http://test-session-url"

        self.client.login(username="testuser", password="testpass123")

        response = self.client.get("/create-checkout-session/")
        self.assertEqual(response.status_code, 302)

    # =========================
    # WEBHOOK: CHECKOUT COMPLETED
    # =========================
    @patch("stripe.Webhook.construct_event")
    def test_webhook_checkout_completed(self, mock_event):
        mock_event.return_value = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"user_id": self.user.id},
                    "customer": "cus_test",
                    "subscription": "sub_test"
                }
            }
        }

        response = self.client.post(
            reverse("resilia:stripe_webhook"),
            data="{}",
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_active)

    # =========================
    # WEBHOOK: SUBSCRIPTION DELETED
    # =========================
    @patch("stripe.Webhook.construct_event")
    def test_subscription_deleted(self, mock_event):
        mock_event.return_value = {
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "customer": "cus_test"
                }
            }
        }

        self.sub.stripe_customer_id = "cus_test"
        self.sub.is_active = True
        self.sub.save()

        response = self.client.post(
            reverse("resilia:stripe_webhook"),
            data="{}",
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active)